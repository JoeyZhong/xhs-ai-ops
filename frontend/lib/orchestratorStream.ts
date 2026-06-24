// Orchestrator-Coordinator SSE 消费器
// 契约：docs/handoff/orchestrator-coordinator-contracts.md §B（done 终止符铁律）
//
// ★ 铁律：本消费循环【只在收到 type==="done" 的事件时 return 收流】。
//   不是 final，不是 awaiting_user，不是 decision_card —— 暂停事件后面一定还跟一个 done。
//   done.status 决定本轮落点：
//     done | cancelled                  → 终态，正常收尾
//     awaiting_user | awaiting_decision → 非终态，调用方据此渲染追问/决策气泡并保会话可续接
import { sseUrl, generateIdempotencyKey } from "./api";
import type { OrchEvent, OrchStatus } from "./api";

export interface ConsumeResult {
  status: OrchStatus;
}

/** 空闲超时默认值：两个事件之间超过该时长仍无任何事件 → 判定为卡死。 */
export const DEFAULT_IDLE_TIMEOUT_MS = 120_000;

/** 空闲超时专用错误，便于调用方区分「卡死」与其它失败。 */
export class OrchestratorStreamTimeout extends Error {
  constructor(public readonly idleMs: number) {
    super(`主助手长时间无响应（超过 ${Math.round(idleMs / 1000)}s 没有任何反馈），已自动断开。`);
    this.name = "OrchestratorStreamTimeout";
  }
}

/**
 * 消费 /converse/stream，每个事件回调 onEvent，直到收到 done 才 resolve。
 * @param body          本轮入参（新会话不带 session_id；续接带 session_id）
 * @param onEvent       每个 §B 事件的回调（调用方据此增量渲染气泡）
 * @param signal        可选 AbortSignal（断连/离开页面时中止）
 * @param idleTimeoutMs 空闲超时：距上一个事件超过该时长无新事件即中止并抛 OrchestratorStreamTimeout
 * @returns             { status }，取自 done.status
 */
export async function consumeOrchestratorStream(
  body: { message: string; goal_id?: string | null; session_id?: string | null },
  onEvent: (e: OrchEvent) => void,
  signal?: AbortSignal,
  idleTimeoutMs: number = DEFAULT_IDLE_TIMEOUT_MS,
): Promise<ConsumeResult> {
  // 内部 controller 合并两路中止源：外部 signal（用户离开/发起新一轮）与空闲超时。
  const ctrl = new AbortController();
  let timedOut = false;
  const onExternalAbort = () => ctrl.abort();
  if (signal) {
    if (signal.aborted) ctrl.abort();
    else signal.addEventListener("abort", onExternalAbort, { once: true });
  }

  // 空闲计时器：每收到一个真实事件就重置。
  // 只按「事件」计时、不按字节——sse-starlette 的 ping 注释能保活 TCP，
  // 但 LLM / 子 agent 卡死时不产出任何事件，正是要靠这个兜底，不被 ping 误喂。
  let timer: ReturnType<typeof setTimeout> | undefined;
  const arm = () => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      timedOut = true;
      ctrl.abort();
    }, idleTimeoutMs);
  };
  const disarm = () => {
    if (timer) clearTimeout(timer);
    timer = undefined;
  };

  arm(); // 覆盖「连接建立 + 首个事件」之前的等待
  try {
    const res = await fetch(sseUrl("/api/v1/orchestrator/converse/stream"), {
      method: "POST",
      // 流式端点同样挂在 IdempotencyRoute 下：写方法缺 Idempotency-Key 会被中间件 428 挡掉。
      // 每轮发一个新 key（对话每次发送都是新 turn，与非流式 /converse 经 apiFetch 的行为一致）。
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": generateIdempotencyKey(),
      },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    if (!res.ok || !res.body) throw new Error(`orchestrator stream failed: ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      // SSE 帧以空行分隔。sse-starlette 默认行尾是 \r\n，故帧分隔符是 \r\n\r\n，
      // 必须同时兼容 \r\n\r\n / \r\r / \n\n —— 否则整条流分不出帧、永远收不到 done，
      // 表现为「连接意外中断（未收到结束信号）」。行内同理按任意行尾切。
      const frames = buf.split(/\r\n\r\n|\r\r|\n\n/);
      buf = frames.pop() ?? "";
      for (const frame of frames) {
        const dataLine = frame.split(/\r\n|\r|\n/).find((l) => l.startsWith("data:"));
        if (!dataLine) continue;
        const payload = dataLine.slice(5).trim();
        if (!payload) continue;
        const evt = JSON.parse(payload) as OrchEvent;
        arm(); // 收到真实事件 → 重置空闲计时器
        onEvent(evt);
        if (evt.type === "done") return { status: evt.status }; // ★ 唯一收流点
      }
    }

    // 走到这里 = 服务端在没发契约规定的 done 终止符就关闭了连接（异常断流）。
    // 不再伪装成正常收尾，抛错让 UI 明确提示，而不是悄悄抹掉「思考中」。
    throw new Error("主助手连接意外中断（未收到结束信号），请重试。");
  } catch (err) {
    if (timedOut) throw new OrchestratorStreamTimeout(idleTimeoutMs);
    throw err;
  } finally {
    disarm();
    if (signal) signal.removeEventListener("abort", onExternalAbort);
  }
}
