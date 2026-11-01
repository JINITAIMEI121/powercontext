# 订单查询规格

## SPEC-001.1 按状态筛选订单
version: 3
status: active

`repository.list_orders(status=...)` 只返回 `status` 完全相等的订单；`status=None` 时返回全部订单，
不得改变返回顺序。金额汇总统一走 `service.total_amount`，保留两位小数。

## SPEC-001.2 按客户筛选订单
version: 2
status: active

`customer_id` 为整数时按客户精确匹配；传入 `customer_id=None` 表示不过滤。
两个条件同时给出时按 AND 组合。

## SPEC-001.3 旧的模糊搜索
version: 1
status: deprecated

历史上允许 `status` 传入前缀做模糊匹配，该行为已在 v2 移除，不得重新引入。
