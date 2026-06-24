-- Phase P3 · 009 · used_angles 三态 schema 迁移
--
-- 关联:
--   - openspec/changes/content-lifecycle-v2/design.md §5（used_angles 三态结构）
--   - openspec/changes/content-lifecycle-v2/tasks.md P3.1
--   - 纯函数等价实现: agents/used_angles.py::normalize_used_angles
--   - 本机 PG 不可达 → 本迁移 reviewed-only，PG 部署后随 migration_runner 执行
--
-- 设计要点:
--   1. 把 goals.data->'used_angles' 里的老字符串元素
--      ["反直觉型"] → [{"angle":"反直觉型","status":"unknown","evidence_count":0,"last_ces":null}]
--   2. 幂等: 已是 object 的元素原样保留(只 wrap 仍是 jsonb string 的元素)
--   3. 只动 goal 行(data ? 'used_angles')，不动 _meta 行
--   4. PG 16 兼容: 用 jsonb_agg + jsonb_typeof，禁用 PG 17 才有的语法


-- 把单个 used_angles 元素规整成三态对象(幂等)
CREATE OR REPLACE FUNCTION _normalize_used_angle_elem(elem jsonb)
RETURNS jsonb
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT CASE
        -- 老字符串元素 → wrap 成 unknown 态对象
        WHEN jsonb_typeof(elem) = 'string' THEN
            jsonb_build_object(
                'angle', elem,
                'status', 'unknown',
                'evidence_count', 0,
                'last_ces', null
            )
        -- 已是对象 → 补全缺省字段，非法 status 归 unknown
        WHEN jsonb_typeof(elem) = 'object' THEN
            jsonb_build_object(
                'angle', COALESCE(elem->>'angle', ''),
                'status', CASE
                    WHEN elem->>'status' IN ('unknown', 'validated_hit', 'sunk')
                        THEN elem->>'status'
                    ELSE 'unknown'
                END,
                'evidence_count', COALESCE((elem->>'evidence_count')::int, 0),
                'last_ces', CASE
                    WHEN elem->>'last_ces' IS NULL THEN 'null'::jsonb
                    ELSE to_jsonb((elem->>'last_ces')::numeric)
                END
            )
        ELSE elem
    END;
$$;


-- 就地规整所有含非空 used_angles 数组的 goal 行
UPDATE goals
SET data = jsonb_set(
        data,
        '{used_angles}',
        (
            SELECT COALESCE(jsonb_agg(_normalize_used_angle_elem(elem)), '[]'::jsonb)
            FROM jsonb_array_elements(data->'used_angles') AS elem
        )
    ),
    rev = rev + 1,
    updated_at = now()
WHERE data ? 'used_angles'
  AND jsonb_typeof(data->'used_angles') = 'array'
  AND jsonb_array_length(data->'used_angles') > 0
  -- 幂等守卫: 只在还存在 string 元素时才改写
  AND EXISTS (
      SELECT 1 FROM jsonb_array_elements(data->'used_angles') AS e
      WHERE jsonb_typeof(e) = 'string'
         OR NOT (e ? 'status')
  );


-- 清理临时函数
DROP FUNCTION IF EXISTS _normalize_used_angle_elem(jsonb);
