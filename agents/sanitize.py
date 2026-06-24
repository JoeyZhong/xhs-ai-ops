"""
Tool 返回数据净化层（LLM 边界防御）。

外部不可信数据（采集到的笔记标题、评论文本、用户生成内容等）进入 LLM
上下文前的统一净化层。配合 agents/base.py 的 <untrusted_data> 标签
和 system prompt 中的安全沙箱指令，构成 spotlighting 防御链。

设计原则：
1. 结构完整性：dict 仍是 dict，list 仍是 list — LLM 能正常解析 JSON
2. 字符串长度受控：单字段超长截断（防 prompt 注入有效载荷）
3. 列表长度受控：超长列表截尾（防数据膨胀和 token 浪费）
4. 递归深度受控：防环形引用 / 恶意嵌套
5. 异常零抛出：所有特殊类型（datetime/numpy/pandas/Exception 等）
   都通过 str() 兜底，单元素异常被隔离
"""

from __future__ import annotations

from typing import Any


_TRUNCATE_MARK = "…<truncated>"


def sanitize_tool_result(data: Any,
                          max_text_len: int = 200,
                          max_list_len: int = 50,
                          max_depth: int = 6,
                          _depth: int = 0) -> Any:
    """
    递归裁剪 tool 返回数据。保留结构，只截短长字符串和长列表。

    Args:
        data:          任意 Python 对象
        max_text_len:  单字符串字段最大长度（默认 200 字符）
        max_list_len:  列表/元组最大保留元素数（默认 50）
        max_depth:     递归深度上限（默认 6）

    Returns:
        裁剪后的同结构数据，可直接 json.dumps。
        所有特殊类型（datetime / bytes / Exception / pandas.DataFrame / numpy
        标量 等）会被 str() 后截断为字符串。

    保证：本函数永远不会抛异常。失败的子节点会被替换为
        '<sanitize-error: ExceptionName>' 占位符。
    """
    # 1. 深度兜底
    if _depth > max_depth:
        return _TRUNCATE_MARK

    # 2. None / bool / 数值（注意 bool 必须先于 int 判断）
    if data is None:
        return None
    if isinstance(data, bool):
        return data
    if isinstance(data, (int, float)):
        return data

    # 3. 字符串
    if isinstance(data, str):
        if len(data) <= max_text_len:
            return data
        return data[:max_text_len] + _TRUNCATE_MARK

    # 4. bytes / bytearray：解码后递归
    if isinstance(data, (bytes, bytearray)):
        try:
            text = bytes(data).decode("utf-8", errors="replace")
        except Exception:
            text = repr(bytes(data))
        return sanitize_tool_result(text, max_text_len, max_list_len,
                                      max_depth, _depth)

    # 5. dict：递归 value，单字段异常隔离
    if isinstance(data, dict):
        result: dict = {}
        for k, v in data.items():
            # key 限长（防恶意超长 key）
            try:
                if isinstance(k, str) and len(k) > max_text_len:
                    safe_key: Any = k[:max_text_len] + _TRUNCATE_MARK
                else:
                    safe_key = k
                result[safe_key] = sanitize_tool_result(
                    v, max_text_len, max_list_len, max_depth, _depth + 1)
            except Exception as e:
                # 子字段失败不连累兄弟节点
                try:
                    fallback_key = str(k)[:max_text_len]
                    result[fallback_key] = f"<sanitize-error: {type(e).__name__}>"
                except Exception:
                    pass  # 连 key 都 str 不出来就放弃
        return result

    # 6. list / tuple：限元素数 + 递归
    if isinstance(data, (list, tuple)):
        try:
            items = list(data)
        except Exception:
            return f"<sanitize-error: cannot iterate {type(data).__name__}>"
        original_len = len(items)
        truncated = original_len > max_list_len
        if truncated:
            items = items[:max_list_len]
        sanitized: list = []
        for item in items:
            try:
                sanitized.append(sanitize_tool_result(
                    item, max_text_len, max_list_len, max_depth, _depth + 1))
            except Exception as e:
                sanitized.append(f"<sanitize-error: {type(e).__name__}>")
        if truncated:
            sanitized.append(
                f"…<{original_len - max_list_len} more items truncated>")
        return sanitized

    # 7. set / frozenset：转 list 后递归（顺序稳定化）
    if isinstance(data, (set, frozenset)):
        try:
            ordered = sorted(data, key=lambda x: str(x))
        except Exception:
            try:
                ordered = list(data)
            except Exception:
                return f"<sanitize-error: cannot iterate {type(data).__name__}>"
        return sanitize_tool_result(ordered, max_text_len, max_list_len,
                                      max_depth, _depth)

    # 8. 其他类型兜底：datetime / Decimal / Path / Exception /
    #    numpy 标量 / pandas DataFrame 等都走这里。统一 str() 后截断。
    #    str() 抛异常的恶意对象 → 统一占位符，与 dict/list 失败路径保持一致。
    try:
        text = str(data)
    except Exception as e:
        return f"<sanitize-error: {type(data).__name__}: {type(e).__name__}>"
    if len(text) > max_text_len:
        text = text[:max_text_len] + _TRUNCATE_MARK
    return text
