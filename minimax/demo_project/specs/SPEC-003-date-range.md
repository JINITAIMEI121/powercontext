# 订单日期区间筛选规格

## SPEC-003.1 支持日期区间筛选
version: 2
status: active

`service.filter_orders` 与 `repository.list_orders` 需要支持 `since` 与 `until` 两个可选参数，
按订单 `order_date`（ISO 日期字符串）做闭区间筛选：`since <= order_date <= until`。
只给出其中一个时按单边界筛选；两者都不给时行为与现在完全一致。

## SPEC-003.2 区间合法性校验
version: 2
status: active

当 `since` 与 `until` 同时给出且 `since > until` 时，必须抛出 `ValueError`，
消息形如 `since must not be after until`。校验发生在筛选之前，任何一条记录都不应被返回。

## SPEC-003.3 输出契约
version: 2
status: active

筛选结果保持列表类型与原有顺序，不得就地修改入参列表；返回的每条记录必须是原字典对象，
不允许复制后丢失字段。
