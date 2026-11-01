# 开发上下文：给订单查询增加 since/until 日期区间筛选，since 大于 until 时抛 ValueError，保持原有顺序不变

## 适用规格
- [SPEC-003.2] 区间合法性校验 (version 2, active) 来源 /Users/eieieiei/Documents/Qoder/2026-10-30/07d55855/minimax-sdd/demo_project/specs/SPEC-003-date-range.md:11 相关度 2.8288
  > 当 `since` 与 `until` 同时给出且 `since > until` 时，必须抛出 `ValueError`，
- [SPEC-003.1] 支持日期区间筛选 (version 2, active) 来源 /Users/eieieiei/Documents/Qoder/2026-10-30/07d55855/minimax-sdd/demo_project/specs/SPEC-003-date-range.md:3 相关度 1.7303
  > `service.filter_orders` 与 `repository.list_orders` 需要支持 `since` 与 `until` 两个可选参数，
- [SPEC-002.1] 参数校验风格 (version 4, active) 来源 /Users/eieieiei/Documents/Qoder/2026-10-30/07d55855/minimax-sdd/demo_project/specs/SPEC-002-validation.md:3 相关度 0.8988
  > 所有查询参数在进入筛选逻辑前完成校验；非法输入抛出 `ValueError` 并在消息中写明参数名与实际值，

## 需要修改的代码
- function `filter_orders` @ src/orders/service.py:6-13 :: filter_orders(orders, status, customer_id) (hash f2c3e4a3903f6449)
- function `list_orders` @ src/orders/repository.py:14-21 :: list_orders(status, customer_id) (hash 585bb2cae157211b)
- function `get_order` @ src/orders/repository.py:24-29 :: get_order(order_id) (hash 68e0c8aa9eb42d21)
- function `total_amount` @ src/orders/service.py:16-18 :: total_amount(orders) -> float (hash 618c5c401c631aa3)

## 验证测试
目标功能测试：
- tests/test_service_date_range.py:21 :: test_invalid_range_raises_value_error
- tests/test_service_date_range.py:34 :: test_repository_list_orders_supports_date_range
- tests/test_service_date_range.py:11 :: test_filter_orders_supports_since_and_until
- tests/test_service_date_range.py:27 :: test_input_list_is_not_mutated
- tests/test_service_date_range.py:16 :: test_filter_orders_single_bound
回归测试：
- tests/test_repository_regression.py:32 :: test_service_filter_matches_repository
- tests/test_repository_regression.py:19 :: test_no_filter_keeps_order
- tests/test_repository_regression.py:11 :: test_customer_filter
- tests/test_repository_regression.py:7 :: test_status_filter
- tests/test_repository_regression.py:15 :: test_combined_filter
- tests/test_repository_regression.py:28 :: test_total_amount
- tests/test_repository_regression.py:23 :: test_get_order

## 任务状态
- （无历史状态）