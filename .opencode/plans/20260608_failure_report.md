# 测试结果报告 — 实施计划

## 背景

当前 E2E 测试有 **TIMING_LOG** 机制做耗时统计，但没有失败原因收集机制。断言函数的 `[FAIL]` 输出混在场景 stdout 里，结束后无法汇总。

## 目标

测试结束后输出"测试结果报告"，包含失败用例编号、名称、失败原因。

## 输出示例

```
==========================================
测试结果报告
==========================================
失败用例: 2 个

  #1  C103_tito_tool
      FAIL: 响应状态码应为 200
      期望: 200, 实际: 502

  #2  N101_basic_chat_trajectory_ns
      FAIL: 响应内容应包含 assistant
      未找到: assistant
      内容: {"error": "timeout"}

==========================================
```

## 设计

复用 TIMING_LOG 模式 → 新增 `FAILURE_LOG`（临时文件）+ `FAILURE_CONTEXT`（环境变量）

```
run_tests.sh ──创建 FAILURE_LOG → export 给子进程
run_layer.sh ──设置 FAILURE_CONTEXT=场景名 → export 给子进程
assert_*()   ──失败时写入 FAILURE_LOG: "场景名|消息|详情"
run_tests.sh ──读取 FAILURE_LOG → print_failure_report()
```

FAILURE_LOG 格式（每行一条，管道符分隔）：

```
场景名|失败消息|详情（期望/实际）
```

## 修改文件清单（7 个文件）

| # | 文件 | 改动 |
|---|------|------|
| 1 | `tests/e2e/utils.sh` | 新增 `log_failure()` 辅助函数，4 个 `assert_*` 失败分支调用它 |
| 2 | `tests/e2e/run_tests.sh` | 新增 `FAILURE_LOG` 临时文件 + `print_failure_report()` + 调用 |
| 3 | `tests/e2e/layers/proxy/run_layer.sh` | `run_scenario()` 设置 `FAILURE_CONTEXT` 并 export |
| 4 | `tests/e2e/layers/nginx/run_layer.sh` | 同上 |
| 5 | `tests/e2e/layers/performance/run_layer.sh` | 同上 |
| 6 | `tests/e2e/layers/archive/run_layer.sh` | 同上 |
| 7 | `tests/e2e/layers/comparison/run_layer.sh` | 同上 |

## 详细改动

### 1. `tests/e2e/utils.sh` — 新增 `log_failure()` + 修改断言函数

在文件顶部（颜色定义之后、变量声明之前），新增：

```bash
log_failure() {
    if [ -n "${FAILURE_LOG:-}" ] && [ -w "${FAILURE_LOG:-}" ]; then
        echo "${FAILURE_CONTEXT:-unknown}|$1|$2" >> "$FAILURE_LOG"
    fi
}
```

修改 4 个断言函数，在 `TESTS_FAILED=$((TESTS_FAILED + 1))` 之后追加 `log_failure` 调用：

#### assert_eq（第 47-52 行 → `else` 分支）

```bash
        log_error "$message"
        echo "    期望: $expected"
        echo "    实际: $actual"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        log_failure "$message" "期望: ${expected}, 实际: ${actual}"
        return 1
```

#### assert_contains（第 68-73 行 → `else` 分支）

```bash
        log_error "$message"
        echo "    未找到: $needle"
        echo "    内容: $haystack"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        log_failure "$message" "未找到: ${needle}"
        return 1
```

#### assert_not_contains（第 89-94 行 → `else` 分支）

```bash
        log_error "$message"
        echo "    不应包含: $needle"
        echo "    内容: $haystack"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        log_failure "$message" "不应包含: ${needle}"
        return 1
```

#### assert_http_status

无需改动（委托给 `assert_eq`，已覆盖）。

### 2. `tests/e2e/run_tests.sh` — FAILURE_LOG 创建 + 报告函数

#### 2.1 新增全局变量（第 34 行 TIMING_LOG="" 之后）

```bash
FAILURE_LOG=""
```

#### 2.2 创建临时文件（main() 中 TIMING_LOG 创建处，约第 225-227 行）

```bash
TIMING_LOG=$(mktemp /tmp/e2e_timing_XXXXXX.log)
FAILURE_LOG=$(mktemp /tmp/e2e_failure_XXXXXX.log)
export TIMING_LOG FAILURE_LOG
trap "rm -f '$TIMING_LOG' '$FAILURE_LOG'" EXIT
```

#### 2.3 新增 `print_failure_report()` 函数（`print_timing_report()` 之后）

```bash
print_failure_report() {
    if [ -z "${FAILURE_LOG:-}" ] || [ ! -f "$FAILURE_LOG" ] || [ ! -s "$FAILURE_LOG" ]; then
        return
    fi

    echo ""
    echo "=========================================="
    echo -e "${RED}测试结果报告${NC}"
    echo "=========================================="

    local fail_count=0
    local prev_scenario=""
    local index=0

    while IFS='|' read -r scenario message detail; do
        if [ "$scenario" != "$prev_scenario" ]; then
            index=$((index + 1))
            echo ""
            echo -e "  ${RED}#${index}  ${scenario}${NC}"
            prev_scenario="$scenario"
            fail_count=$((fail_count + 1))
        fi
        echo "      FAIL: ${message}"
        [ -n "$detail" ] && echo "      ${detail}"
    done < "$FAILURE_LOG"

    echo ""
    echo "──────────────────────────────────────────"
    echo -e "  失败用例: ${RED}${fail_count} 个${NC}"
    echo "=========================================="
}
```

#### 2.4 调用 `print_failure_report()`

在以下位置（均在 `print_timing_report` 调用之后）追加调用：

- `print_final_summary()` 中，`print_timing_report` 之后
- 单层层模式（`--layer`）：`print_timing_report` 之后
- 全层运行模式：末尾 `print_timing_report` 之后

### 3. 五个 `layers/*/run_layer.sh` — 设置 FAILURE_CONTEXT

在每个层的 `run_scenario()` 函数中，`bash "$scenario_file"` 之前添加一行：

```bash
export FAILURE_CONTEXT="${scenario_name}"
```

以 proxy 层为例（第 27-28 行之间）：

```bash
    export FAILURE_CONTEXT="${scenario_name}"
    local start_ts=$(date +%s)
    if bash "$scenario_file"; then
```

其余四个层（nginx, performance, archive, comparison）做相同改动位置（各自的 `bash "$scenario_file"` 之前）。

---

## 验证

1. `bash -n tests/e2e/utils.sh` — 语法检查
2. `bash -n tests/e2e/run_tests.sh` — 语法检查
3. `bash -n tests/e2e/layers/*/run_layer.sh` — 各层语法检查
4. 运行 `./run_tests.sh --layer proxy --only P101` 确认正常执行
5. 人为制造失败（如改个断言的期望值），确认报告正确输出

## 不变的部分

- TIMING_LOG 耗时报告：不受影响
- --all / --only / --skip 场景过滤：不受影响
- comparison 层独立的 print_final_summary：不受影响（它有自己的计数）
