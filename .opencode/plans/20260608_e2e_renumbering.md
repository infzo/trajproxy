# E2E 场景编号重新设计方案

> 执行模式: 手动逐步执行，每步可验证
> 预计影响范围: ~65 文件
> 预计耗时: 2-3 小时

---

## 一、变更概览

| 维度 | 现状 | 目标 |
|------|------|------|
| 前缀含义 | F=功能, C=对比, A=归档, P=性能 | **N**=Nginx层, **P**=Proxy层, **A**=Archive层, **C**=Comparison层, **T**=Performance层 |
| 前缀与层对应 | F 跨 nginx+proxy 两层 | 1:1 映射，前缀即层 |
| SKIP_PREFIXES 默认 | `P,A`（跳过性能+归档） | `T,A`（跳过性能+归档，T=Performance 不再与 P=Proxy 冲突） |
| --layer performance | 不支持（未注册 case 分支） | 支持 `--layer performance` |

---

## 二、新旧编号映射总表

### A. 前缀规则

```
前缀字母 = 执行层（唯一对应一个 layer 目录）
百位数字 = 功能领域（同一领域占一个 xx 范围，每域可容纳 99 个用例）
```

| 前缀 | 层 | 说明 | 默认跳过 |
|------|----|------|----------|
| N | Nginx (port 12345) | 入口冒烟 | 否 |
| P | Proxy (port 12300) | 功能测试 | 否 |
| A | Archive | 归档测试 | 是 |
| C | Comparison | 对比测试 | 否 |
| T | Performance (port 12345) | 性能测试 | 是 |

### B. Proxy 层功能领域划分（P 系列）

| 十位块 | 功能领域 | 用例数 | 来源 |
|--------|----------|--------|------|
| P1xx | Model Management（CRUD/注册/懒加载/LRU/限流） | 10 | 原 F101-F107, F112-F115 归入此域 |
| P2xx | Request Processing（参数透传/过滤/覆盖/自定义解析器/TITO冒烟） | 8 | 原 F108-F119 重编号 |
| P3xx | Trajectory（轨迹捕获/一致性/查询API） | 9 | 原 F201-F211 重编号 |
| P4xx | Multi-turn Cache（TITO 多轮缓存 + 边界条件） | 11 | 原 F301-F311 重编号 |

---

## 三、逐场景编号变更明细

### N 系列 - Nginx 层（原 F203/F204）

| 新 ID | 新文件名 | 原 ID | 原文件名 |
|-------|----------|-------|----------|
| N101 | N101_basic_chat_trajectory_ns.sh | F203 | F203_basic_chat_trajectory_ns.sh |
| N102 | N102_stream_chat_trajectory.sh | F204 | F204_stream_chat_trajectory.sh |

### P1xx - Model Management（原 F101-F115）

| 新 ID | 新文件名 | 原 ID | 原文件名 |
|-------|----------|-------|----------|
| P101 | P101_model_crud.sh | F101 | F101_model_crud.sh |
| P102 | P102_pangu_format.sh | F102 | F102_pangu_format.sh |
| P103 | P103_duplicate_register.sh | F103 | F103_duplicate_register.sh |
| P104 | P104_delete_preset_model.sh | F104 | F104_delete_preset_model.sh |
| P105 | P105_model_not_registered.sh | F105 | F105_model_not_registered.sh |
| P106 | P106_invalid_model_param.sh | F106 | F106_invalid_model_param.sh |
| P107 | P107_concurrent_limit.sh | F107 | F107_concurrent_limit.sh |
| P108 | P108_processor_lazy_load.sh | F112 | F112_processor_lazy_load.sh |
| P109 | P109_lru_cache_hit.sh | F113 | F113_lru_cache_hit.sh |
| P110 | P110_preset_model_lazy_load.sh | F114 | F114_preset_model_lazy_load.sh |
| P111 | P111_lru_eviction.sh | F115 | F115_lru_eviction.sh |

### P2xx - Request Processing（原 F108-F119）

| 新 ID | 新文件名 | 原 ID | 原文件名 |
|-------|----------|-------|----------|
| P201 | P201_params_passthrough_direct.sh | F108 | F108_params_passthrough_direct.sh |
| P202 | P202_params_filter_tito.sh | F109 | F109_params_filter_tito.sh |
| P203 | P203_logprobs_force_override.sh | F110 | F110_logprobs_force_override.sh |
| P204 | P204_chat_template_kwargs.sh | F111 | F111_chat_template_kwargs.sh |
| P205 | P205_custom_tool_parser.sh | F116 | F116_custom_tool_parser.sh |
| P206 | P206_custom_reasoning_parser.sh | F117 | F117_custom_reasoning_parser.sh |
| P207 | P207_tito_token_ns.sh | F118 | F118_tito_token_ns.sh |
| P208 | P208_tito_token_s.sh | F119 | F119_tito_token_s.sh |

### P3xx - Trajectory（原 F201-F211，F203/F204 移入 N 层）

| 新 ID | 新文件名 | 原 ID | 原文件名 |
|-------|----------|-------|----------|
| P301 | P301_trajectory_basic.sh | F201 | F201_trajectory_basic.sh |
| P302 | P302_eos_consistency.sh | F202 | F202_eos_consistency.sh |
| P303 | P303_tito_stream_ns_trajectory.sh | F205 | F205_tito_stream_ns_trajectory.sh |
| P304 | P304_direct_stream_ns_consistency.sh | F206 | F206_direct_stream_ns_consistency.sh |
| P305 | P305_pangu_trajectory.sh | F207 | F207_pangu_trajectory.sh |
| P306 | P306_tito_tool_trajectory.sh | F208 | F208_tito_tool_trajectory.sh |
| P307 | P307_trajectories_api.sh | F209 | F209_trajectories_api.sh |
| P308 | P308_trajectory_fields.sh | F210 | F210_trajectory_fields.sh |
| P309 | P309_trajectory_field_crosscheck.sh | F211 | F211_trajectory_field_crosscheck.sh |

### P4xx - Multi-turn Cache（原 F301-F311，修复重名）

| 新 ID | 新文件名 | 原 ID | 原文件名 | 变化说明 |
|-------|----------|-------|----------|----------|
| P401 | P401_cache_ns_3turn.sh | F301 | F301_cache_ns_3turn.sh | 仅前缀 |
| P402 | P402_cache_ns_tool_2turn.sh | F302 | F302_cache_ns_tool_2turn.sh | 仅前缀 |
| P403 | P403_cache_tr_3turn_ns.sh | F303 | F303_cache_tr_3turn.sh | **重命名加 _ns** |
| P404 | P404_cache_tr_3turn_stream.sh | F304 | F304_cache_tr_3turn.sh | **重命名加 _stream** |
| P405 | P405_cache_ns_reasoning_3turn.sh | F305 | F305_cache_ns_reasoning_3turn.sh | 仅前缀 |
| P406 | P406_cache_s_3turn.sh | F306 | F306_cache_s_3turn.sh | 仅前缀 |
| P407 | P407_cache_mixed_3turn.sh | F307 | F307_cache_mixed_3turn.sh | 仅前缀 |
| P408 | P408_cache_smoke_ns_s.sh | F308 | F308_cache_smoke_ns_s.sh | 仅前缀 |
| P409 | P409_cache_nosession_2turn.sh | F309 | F309_cache_nosession_2turn.sh | 仅前缀 |
| P410 | P410_cache_nothink_2turn.sh | F310 | F310_cache_nothink_2turn.sh | 仅前缀 |
| P411 | P411_cache_diff_tools_2turn.sh | F311 | F311_cache_diff_tools_2turn.sh | 仅前缀 |

### C 系列 - Comparison 层（不变）

全部 14 个场景保持不变：C101-C107, C201-C207。

### A 系列 - Archive 层（不变）

全部 4 个场景保持不变：A100-A103。

### T 系列 - Performance 层（原 P 系列）

| 新 ID | 新文件名 | 原 ID | 原文件名 |
|-------|----------|-------|----------|
| T101 | T101_stability.sh | P100 | P100_stability.sh |
| T102 | T102_concurrent.sh | P101 | P101_concurrent.sh |
| T103 | T103_streaming_concurrent.sh | P102 | P102_streaming_concurrent.sh |

---

## 四、每个场景文件内部需要更新的 5 处

对每个被重编号的场景文件，需更新以下 5 处内容：

| 序号 | 位置 | 示例（F203→N101） |
|------|------|-------------------|
| 1 | 文件名 | `git mv F203_xxx.sh N101_xxx.sh` |
| 2 | 文件头注释 | `# 场景 F203:` → `# 场景 N101:` |
| 3 | echo 横幅 | `echo "场景 F203:` → `echo "场景 N101:` |
| 4 | SCENARIO_ID 提取正则 | `grep -oE '[FP][0-9]+'` → `grep -oE '[A-Z][0-9]+'` |
| 5 | RUN_ID / session-f* 前缀 | `run-f203` → `run-n101`; `session-f203` → `session-n101` |

---

## 五、执行步骤

> 执行方式：分阶段手动执行，每阶段可独立验证、可中断

### Phase 1: 文件重命名（约 42 个 git mv）

#### 1.1 Nginx 层 (N 系列，2 个)

```sh
cd tests/e2e/layers/nginx/scenarios/
git mv F203_basic_chat_trajectory_ns.sh N101_basic_chat_trajectory_ns.sh
git mv F204_stream_chat_trajectory.sh    N102_stream_chat_trajectory.sh
```

#### 1.2 Proxy 层 - P1xx（Model Management，11 个）

```sh
cd tests/e2e/layers/proxy/scenarios/
git mv F101_model_crud.sh             P101_model_crud.sh
git mv F102_pangu_format.sh           P102_pangu_format.sh
git mv F103_duplicate_register.sh     P103_duplicate_register.sh
git mv F104_delete_preset_model.sh    P104_delete_preset_model.sh
git mv F105_model_not_registered.sh   P105_model_not_registered.sh
git mv F106_invalid_model_param.sh    P106_invalid_model_param.sh
git mv F107_concurrent_limit.sh       P107_concurrent_limit.sh
git mv F112_processor_lazy_load.sh    P108_processor_lazy_load.sh
git mv F113_lru_cache_hit.sh          P109_lru_cache_hit.sh
git mv F114_preset_model_lazy_load.sh P110_preset_model_lazy_load.sh
git mv F115_lru_eviction.sh           P111_lru_eviction.sh
```

#### 1.3 Proxy 层 - P2xx（Request Processing，8 个）

```sh
cd tests/e2e/layers/proxy/scenarios/
git mv F108_params_passthrough_direct.sh  P201_params_passthrough_direct.sh
git mv F109_params_filter_tito.sh         P202_params_filter_tito.sh
git mv F110_logprobs_force_override.sh    P203_logprobs_force_override.sh
git mv F111_chat_template_kwargs.sh       P204_chat_template_kwargs.sh
git mv F116_custom_tool_parser.sh         P205_custom_tool_parser.sh
git mv F117_custom_reasoning_parser.sh    P206_custom_reasoning_parser.sh
git mv F118_tito_token_ns.sh              P207_tito_token_ns.sh
git mv F119_tito_token_s.sh               P208_tito_token_s.sh
```

#### 1.4 Proxy 层 - P3xx（Trajectory，9 个）

```sh
cd tests/e2e/layers/proxy/scenarios/
git mv F201_trajectory_basic.sh            P301_trajectory_basic.sh
git mv F202_eos_consistency.sh             P302_eos_consistency.sh
git mv F205_tito_stream_ns_trajectory.sh   P303_tito_stream_ns_trajectory.sh
git mv F206_direct_stream_ns_consistency.sh P304_direct_stream_ns_consistency.sh
git mv F207_pangu_trajectory.sh            P305_pangu_trajectory.sh
git mv F208_tito_tool_trajectory.sh        P306_tito_tool_trajectory.sh
git mv F209_trajectories_api.sh            P307_trajectories_api.sh
git mv F210_trajectory_fields.sh           P308_trajectory_fields.sh
git mv F211_trajectory_field_crosscheck.sh P309_trajectory_field_crosscheck.sh
```

#### 1.5 Proxy 层 - P4xx（Multi-turn Cache，11 个，P403/P404 重命名）

```sh
cd tests/e2e/layers/proxy/scenarios/
git mv F301_cache_ns_3turn.sh              P401_cache_ns_3turn.sh
git mv F302_cache_ns_tool_2turn.sh         P402_cache_ns_tool_2turn.sh
git mv F303_cache_tr_3turn.sh              P403_cache_tr_3turn_ns.sh
git mv F304_cache_tr_3turn.sh              P404_cache_tr_3turn_stream.sh
git mv F305_cache_ns_reasoning_3turn.sh    P405_cache_ns_reasoning_3turn.sh
git mv F306_cache_s_3turn.sh               P406_cache_s_3turn.sh
git mv F307_cache_mixed_3turn.sh           P407_cache_mixed_3turn.sh
git mv F308_cache_smoke_ns_s.sh            P408_cache_smoke_ns_s.sh
git mv F309_cache_nosession_2turn.sh       P409_cache_nosession_2turn.sh
git mv F310_cache_nothink_2turn.sh         P410_cache_nothink_2turn.sh
git mv F311_cache_diff_tools_2turn.sh      P411_cache_diff_tools_2turn.sh
```

#### 1.6 Performance 层（T 系列，3 个）

```sh
cd tests/e2e/layers/performance/scenarios/
git mv P100_stability.sh            T101_stability.sh
git mv P101_concurrent.sh           T102_concurrent.sh
git mv P102_streaming_concurrent.sh T103_streaming_concurrent.sh
```

#### 1.7 提交

```sh
cd /Users/liujiang/Workspace/Code/trajproxy
git add -A
git commit -m "refactor(e2e): rename scenario files to layer-based prefix scheme

N=Nginx, P=Proxy, A=Archive, C=Comparison, T=Performance
Each prefix now maps 1:1 to a layer directory."
```

---

### Phase 2: 场景文件内部 ID 更新

> 使用批量 sed 替换，每个文件更新注释+横幅+提取正则+运行时值

#### 2.1 全局修复：SCENARIO_ID 提取正则（18 个文件）

当前正则 `[FP][0-9]+` 无法识别新前缀 N 和 T，必须统一为 `[A-Z][0-9]+`。

```sh
# 精确替换（只影响 grep -oE 那行）
grep -rl "grep -oE '\[FP\]\[0-9\]+'" tests/e2e/layers/nginx/scenarios/ \
  tests/e2e/layers/proxy/scenarios/ \
  tests/e2e/layers/performance/scenarios/ \
| xargs sed -i '' "s/grep -oE '\[FP\]\[0-9\]+'/grep -oE '[A-Z][0-9]+'/g"

# 同时统一 Comparison 层（可选，建议一并处理）
grep -rl "grep -oE 'C\[0-9\]+'" tests/e2e/layers/comparison/scenarios/ \
| xargs sed -i '' "s/grep -oE 'C\[0-9\]+'/grep -oE '[A-Z][0-9]+'/g"
```

> **注意**：macOS sed 的 `-i ''` 语法；Linux 下改为 `-i`（无空格）。

#### 2.2 替换旧 ID：Nginx 层（2 个文件）

```sh
cd tests/e2e/layers/nginx/scenarios/
sed -i '' '0,/F203/{s/F203/N101/g; s/场景 N101: 基础/场景 N101: 基础/}' N101_basic_chat_trajectory_ns.sh
sed -i '' '0,/F204/{s/F204/N102/g; s/场景 N102: 流式/场景 N102: 流式/}' N102_stream_chat_trajectory.sh
# 简洁版（全文件替换，场景 ID 在文件中唯一）：
sed -i '' 's/F203/N101/g' N101_basic_chat_trajectory_ns.sh
sed -i '' 's/F204/N102/g' N102_stream_chat_trajectory.sh
```

> 同时更新 RUN_ID（若存在）。

#### 2.3 替换旧 ID：Proxy 层 - P1xx（10 个文件）

| 文件 | 替换 |
|------|------|
| P101_*.sh | `F101` → `P101` |
| P102_*.sh | `F102` → `P102` |
| P103_*.sh | `F103` → `P103` + `run-f103` → `run-p103` |
| P104_*.sh | `F104` → `P104` |
| P105_*.sh | `F105` → `P105` |
| P106_*.sh | `F106` → `P106` |
| P107_*.sh | `F107` → `P107` + `run-f107` → `run-p107` |
| P108_*.sh | `F112` → `P108` + `session-f215-` → `session-p108-` |
| P109_*.sh | `F113` → `P109` + `session-f216-` → `session-p109-` |
| P110_*.sh | `F114` → `P110` + `session-f217-` → `session-p110-` |
| P111_*.sh | `F115` → `P111` + `session-f218-` → `session-p111-` |

```sh
cd tests/e2e/layers/proxy/scenarios/
for old_new in "F101:P101" "F102:P102" "F103:P103" "F104:P104" "F105:P105" "F106:P106" "F107:P107"; do
  old="${old_new%%:*}"; new="${old_new##*:}"
  sed -i '' "s/${old}/${new}/g" ${new}_*.sh
done
# P108-P111: ID + session 修复
sed -i '' 's/\bF112\b/P108/g; s/session-f215-/session-p108-/g' P108_processor_lazy_load.sh
sed -i '' 's/\bF113\b/P109/g; s/session-f216-/session-p109-/g' P109_lru_cache_hit.sh
sed -i '' 's/\bF114\b/P110/g; s/session-f217-/session-p110-/g' P110_preset_model_lazy_load.sh
sed -i '' 's/\bF115\b/P111/g; s/session-f218-/session-p111-/g' P111_lru_eviction.sh
```

#### 2.4 替换旧 ID：Proxy 层 - P2xx（8 个文件）

| 文件 | 替换 |
|------|------|
| P201 | `F108` → `P201` |
| P202 | `F109` → `P202` |
| P203 | `F110` → `P203` |
| P204 | `F111` → `P204` |
| P205 | `F116` → `P205` |
| P206 | `F117` → `P206` |
| P207 | `F118` → `P207` + `run-f118` → `run-p207` |
| P208 | `F119` → `P208` + `run-f119` → `run-p208` |

```sh
cd tests/e2e/layers/proxy/scenarios/
sed -i '' 's/\bF108\b/P201/g' P201_params_passthrough_direct.sh
sed -i '' 's/\bF109\b/P202/g' P202_params_filter_tito.sh
sed -i '' 's/\bF110\b/P203/g' P203_logprobs_force_override.sh
sed -i '' 's/\bF111\b/P204/g' P204_chat_template_kwargs.sh
sed -i '' 's/\bF116\b/P205/g' P205_custom_tool_parser.sh
sed -i '' 's/\bF117\b/P206/g' P206_custom_reasoning_parser.sh
sed -i '' 's/\bF118\b/P207/g; s/run-f118/run-p207/g' P207_tito_token_ns.sh
sed -i '' 's/\bF119\b/P208/g; s/run-f119/run-p208/g' P208_tito_token_s.sh
```

#### 2.5 替换旧 ID：Proxy 层 - P3xx（9 个文件）

| 文件 | 替换 |
|------|------|
| P301 | `F201` → `P301` |
| P302 | `F202` → `P302` |
| P303 | `F205` → `P303` |
| P304 | `F206` → `P304` + 清理 F014 遗留引用 |
| P305 | `F207` → `P305` |
| P306 | `F208` → `P306` + `run-f208` → `run-p306` |
| P307 | `F209` → `P307` |
| P308 | `F210` → `P308` |
| P309 | `F211` → `P309` + `run-f211` → `run-p309` |

```sh
cd tests/e2e/layers/proxy/scenarios/
sed -i '' 's/\bF201\b/P301/g' P301_trajectory_basic.sh
sed -i '' 's/\bF202\b/P302/g' P302_eos_consistency.sh
sed -i '' 's/\bF205\b/P303/g' P303_tito_stream_ns_trajectory.sh
sed -i '' 's/\bF206\b/P304/g; s/与 F014 区别/与 P207（原F014）区别/g' P304_direct_stream_ns_consistency.sh
sed -i '' 's/\bF207\b/P305/g' P305_pangu_trajectory.sh
sed -i '' 's/\bF208\b/P306/g; s/run-f208/run-p306/g' P306_tito_tool_trajectory.sh
sed -i '' 's/\bF209\b/P307/g' P307_trajectories_api.sh
sed -i '' 's/\bF210\b/P308/g' P308_trajectory_fields.sh
sed -i '' 's/\bF211\b/P309/g; s/run-f211/run-p309/g' P309_trajectory_field_crosscheck.sh
```

#### 2.6 替换旧 ID：Proxy 层 - P4xx（11 个文件，修复 RUN_ID）

| 文件 | 替换 |
|------|------|
| P401 | `F301` → `P401` + `run-f301` → `run-p401` |
| P402 | `F302` → `P402` + `run-f302` → `run-p402` |
| P403 | `F303` → `P403` + `run-f303` → `run-p403` |
| P404 | `F304` → `P404` + `run-f304` → `run-p404` |
| P405-P411 | `F305`~`F311` → `P405`~`P411` + `run-f305`~`run-f311` → `run-p405`~`run-p411` |

```sh
cd tests/e2e/layers/proxy/scenarios/
for i in 01 02 03 04 05 06 07 08 09 10 11; do
  old_f="F3$(printf '%02d' $((10#$i)))"   # F301-F311
  new_p="P4$(printf '%02d' $((10#$i)))"   # P401-P411
  for f in ${new_p}_*.sh; do
    [ -f "$f" ] || continue
    sed -i '' "s/\b${old_f}\b/${new_p}/g; s/run-f${old_f:1}/run-p${new_p:1}/g" "$f"
    # run-f301 → run-p401 精确替换
    sed -i '' "s/run-f${old_f:1}/run-${new_p,,}/g" "$f"
  done
done
# 更清晰的精确替换（避免上面的循环出问题）：
sed -i '' 's/\bF301\b/P401/g; s/run-f301/run-p401/g' P401_cache_ns_3turn.sh
sed -i '' 's/\bF302\b/P402/g; s/run-f302/run-p402/g' P402_cache_ns_tool_2turn.sh
sed -i '' 's/\bF303\b/P403/g; s/run-f303/run-p403/g' P403_cache_tr_3turn_ns.sh
sed -i '' 's/\bF304\b/P404/g; s/run-f304/run-p404/g' P404_cache_tr_3turn_stream.sh
sed -i '' 's/\bF305\b/P405/g; s/run-f305/run-p405/g' P405_cache_ns_reasoning_3turn.sh
sed -i '' 's/\bF306\b/P406/g; s/run-f306/run-p406/g' P406_cache_s_3turn.sh
sed -i '' 's/\bF307\b/P407/g; s/run-f307/run-p407/g' P407_cache_mixed_3turn.sh
sed -i '' 's/\bF308\b/P408/g; s/run-f308/run-p408/g' P408_cache_smoke_ns_s.sh
sed -i '' 's/\bF309\b/P409/g; s/run-f309/run-p409/g' P409_cache_nosession_2turn.sh
sed -i '' 's/\bF310\b/P410/g; s/run-f310/run-p410/g' P410_cache_nothink_2turn.sh
sed -i '' 's/\bF311\b/P411/g; s/run-f311/run-p411/g' P411_cache_diff_tools_2turn.sh
```

#### 2.7 替换旧 ID：Performance 层（3 个文件）

```sh
cd tests/e2e/layers/performance/scenarios/
sed -i '' 's/\bP100\b/T101/g' T101_stability.sh
sed -i '' 's/\bP101\b/T102/g' T102_concurrent.sh
sed -i '' 's/\bP102\b/T103/g' T103_streaming_concurrent.sh
```

#### 2.8 提交

```sh
cd /Users/liujiang/Workspace/Code/trajproxy
git add -A
git commit -m "refactor(e2e): update internal scenario IDs and runtime values

- Update header comments and echo banners with new IDs
- Fix SCENARIO_ID extraction regex: [FP] -> [A-Z] (supports N, P, T prefixes)
- Fix stale session-f2xx prefixes (P108-P111) to match new IDs
- Fix stale RUN_ID values to match new IDs
- Clean up obsolete F014 cross-reference in P304"
```

---

### Phase 3: 编排脚本更新

#### 3.1 更新 `tests/e2e/run_tests.sh`

**关键变更（4 处）：**

| 位置 | 当前 | 新内容 |
|------|------|--------|
| line 36 注释 | `P=性能测试, A=归档测试` | `T=性能测试, A=归档测试` |
| line 38 默认值 | `SKIP_PREFIXES="${SKIP_PREFIXES:-P,A}"` | `SKIP_PREFIXES="${SKIP_PREFIXES:-T,A}"` |
| line 54 帮助文本 | `SKIP_PREFIXES=P,A` | `SKIP_PREFIXES=T,A` |
| line 10-11, 49 帮助文本 | `F100`, `F101`, `F200` | `P101`, `N101` |

**新增 performance 层支持（run_tests.sh:290 附近添加）：**

```bash
            # 在 comparison|4) ;; 之后添加：
            performance|5)
                run_layer_with_filter "${SCRIPT_DIR}/layers/performance"
                TOTAL_SCENARIOS=$?
                ;;
```

**新增 Layer 5 执行块（全量运行模式，约 line 334 之后）：**

```bash
        echo ""
        echo "=========================================="
        echo -e "${BLUE}Layer 5: Performance (port 12345)${NC}"
        echo "=========================================="
        run_layer_with_filter "${SCRIPT_DIR}/layers/performance" || true
```

#### 3.2 更新各层 `run_layer.sh` 帮助文本

| 文件 | 当前示例 ID | 新示例 ID |
|------|-----------|----------|
| `nginx/run_layer.sh` | `F203` | `N101` |
| `proxy/run_layer.sh` | `F101`, `F102` | `P101`, `P102` |
| `archive/run_layer.sh` | 不变 | 不变 |
| `comparison/run_layer.sh` | 不变 | 不变 |
| `performance/run_layer.sh` | `P101`, `P102` | `T101`, `T102` |

同时更新 `proxy/run_layer.sh:51` 注释：
```bash
# 当前: # 搜索匹配的场景文件（如 F101 匹配 F101_model_crud.sh）
# 改为: # 搜索匹配的场景文件（如 P101 匹配 P101_model_crud.sh）
```

#### 3.3 提交

```sh
git add -A
git commit -m "refactor(e2e): update orchestrator scripts for new prefix scheme

- Change default SKIP_PREFIXES: P,A -> T,A
- Add --layer performance support (Layer 5)
- Update all run_layer.sh usage examples with new scenario IDs
- Fix stale help text references (F100/F200 -> P101/N101)"
```

---

### Phase 4: 文档更新

#### 4.1 `docs/TEST_CASE_CATALOG.md`（全面重写）

需要更新：
- 全文所有旧 ID（F1xx~F3xx, P100-P102）→ 新 ID
- 原编号列增加本次迁移记录（`原 F101 → 现 P101`）
- 表格按新的 P1xx~P4xx 领域重排，N/C/A/T 各成独立章节
- 删除/合并历史遗留的旧 ID 映射说明

**建议重写骨架：**

```markdown
# E2E 测试场景目录

## 前缀说明
| 前缀 | 层 | 说明 |
|------|----|------|
| N | Nginx | 入口冒烟（2 个） |
| P | Proxy | 功能测试（38 个，分 4 个领域） |
| A | Archive | 归档测试（4 个） |
| C | Comparison | 对比测试（14 个） |
| T | Performance | 性能测试（3 个） |

## P 系列 - Proxy 层
### P1xx - Model Management (10 cases)
...
### P2xx - Request Processing (8 cases)
...
### P3xx - Trajectory (9 cases)
...
### P4xx - Multi-turn Cache (11 cases)
...

## N 系列 - Nginx 层 (2 cases)
...

## A 系列 - Archive 层 (4 cases)
...

## C 系列 - Comparison 层 (14 cases)
...

## T 系列 - Performance 层 (3 cases)
...
```

#### 4.2 `docs/e2e_case_desc.md`（873 行，全面替换 ID）

将所有场景标题和内部旧 ID 替换：

```sh
cd /Users/liujiang/Workspace/Code/trajproxy
# 使用与 Phase 2 相同的 sed 映射表，批量替换文档中的旧 ID
# 建议写一个临时脚本一次性处理（略）
```

#### 4.3 `docs/RELEASE.md`（添加说明，不改历史记录）

在文件顶部添加：

```markdown
> 本文件记录历史发布信息，场景中引用的场景 ID 为当时使用的编号。
> 2026-06-08 编号体系重构后，请参考 docs/TEST_CASE_CATALOG.md 获取当前编号。
```

#### 4.4 提交

```sh
git add -A
git commit -m "docs: update scenario catalog and descriptions for new ID scheme

- Rewrite TEST_CASE_CATALOG.md with new N/P/A/C/T structure
- Update e2e_case_desc.md with all new scenario IDs
- Add deprecation note to RELEASE.md for old ID references"
```

---

### Phase 5: 验证

```sh
cd /Users/liujiang/Workspace/Code/trajproxy

echo "=== 验证 1: nginx 层不应有 F 前缀文件 ==="
ls tests/e2e/layers/nginx/scenarios/ | grep "^F" || echo "PASS: 无 F 前缀文件"

echo "=== 验证 2: proxy 层不应有 F 前缀文件 ==="
ls tests/e2e/layers/proxy/scenarios/ | grep "^F" || echo "PASS: 无 F 前缀文件"

echo "=== 验证 3: performance 层不应有 P100/P101/P102 文件 ==="
ls tests/e2e/layers/performance/scenarios/ | grep "^P" || echo "PASS: 无 P 前缀文件"

echo "=== 验证 4: 场景文件中不应有旧 ID 引用（允许 RELEASE.md 历史记录）==="
grep -rn "F1[0-9][0-9]\|F2[0-9][0-9]\|F3[0-9][0-9]" tests/e2e/layers/ || echo "PASS: 层目录无旧 ID"

echo "=== 验证 5: SCENARIO_ID 正则已统一为 [A-Z][0-9]+ ==="
grep -rn "grep -oE" tests/e2e/layers/ | grep -v '\[A-Z\]' || echo "PASS: 正则统一"

echo "=== 验证 6: 文件名无重复（P403/P404 已区分）==="
ls tests/e2e/layers/proxy/scenarios/ | sort | uniq -d || echo "PASS: 无重名文件"

echo "=== 验证 7: SKIP_PREFIXES 默认值已更新 ==="
grep "SKIP_PREFIXES.*:-" tests/e2e/run_tests.sh

echo "=== 验证 8: --layer performance 已注册 ==="
grep "performance|5" tests/e2e/run_tests.sh && echo "PASS: performance 层已注册"

echo "=== 验证 9: 运行 Nginx 层基础验证（快速）==="
SKIP_PREFIXES='' ./tests/e2e/run_tests.sh --layer nginx
```

---

## 六、回滚方案

执行前打快照 tag，出问题随时回滚：

```sh
# 执行 Phase 1 前
git tag -a v-pre-renumber -m "Pre-renumber checkpoint"

# 回滚（谨慎！会丢弃本地改动）
git reset --hard v-pre-renumber
```

---

## 七、风险清单

| 风险 | 严重度 | 缓解 |
|------|--------|------|
| `[A-Z][0-9]+` 误匹配其他大写+数字标识 | 低 | 场景 ID 在脚本中唯一用于自标识，无其他同类标识 |
| RUN_ID 遗漏导致轨迹查询失败 | 高 | Phase 2 明确列出 17 个文件，Phase 5 跑请求验证 |
| `SKIP_PREFIXES` 默认 P→T 遗漏 | 高 | Phase 3 第一项处理，Phase 5.7 验证 |
| `--layer performance` 未注册 | 中 | Phase 3 一并处理 |
| sed 正则 `\b` 在 macOS 不工作 | 中 | macOS sed 不支持 `\b`，改用 `\b` 替代方案 `[^a-zA-Z]'${old}'[^a-zA-Z]` 或直接不用 word boundary（ID 在文件中唯一，全局替换安全） |
| 42 个文件 mv 可能遗漏某些 | 低 | Phase 5.1-5.3 检查 F/P 前缀文件应为零 |
