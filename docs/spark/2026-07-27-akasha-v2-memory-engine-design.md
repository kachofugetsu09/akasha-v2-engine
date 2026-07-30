# Akasha V2 显式记忆引擎设计规格

- 状态：已实现并通过验收
- 日期：2026-07-27（首版），2026-07-30（更新至反馈与双路读出）
- 目标仓库：`akasha-v2-engine`
- 目标插件 ID：`akasha`
- 设计基线：`/mnt/data/coding/akasha-v2` 的稳定 V8 机制与冻结评估结果

## 1. 目标

Akasha V2 是一个独立、可在线运行、可确定性重放的显式记忆引擎。它把每轮
`user + assistant` 对话编码成稀疏索引，通过共同激活、时间关系、局部扩散、
可塑性和自适应遗忘形成可持续生长的关联记忆。

必须同时满足：

1. 召回效果与冻结的 Akasha V8 基线一致；
2. 线上逐轮写入与离线按相同历史重放得到等价领域状态；
3. 符合 Akasic Agent 当前 `MemoryEngine` 和 `MemoryPlugin` 接口；
4. 所有学习来自对话、检索和历史复现，不依赖用户标签、外部确认或
   fast/slow/tag/reinforce 双轨机制；
5. 数据库可以由 `sessions.db`、消息 embedding、算法版本和配置确定性重建；
6. 代码可以作为整个 `plugins/akasha` 的替代来源；
7. 仓库为独立 Git 仓库 `akasha-v2-engine`。

## 2. 非目标

- 修改 `akasic-agent`；
- 迁移旧 Akasha sidecar schema；
- 训练或微调 embedding 模型；
- 把脑区名称直接映射成 Python 类；
- 单独返回 direct-dense 或 direct-BM25 结果通道；
- 用户手工 remember、forget、tag 或 reinforce；
- 全局 Hopfield 多吸引子动力学；
- 后台物理删除和压缩陈旧节点；
- 声称系统已经完整复现海马体或证明了 AGI。

Agent 发起的 Message-level feedback（remember / forget）是因果事实源
的一部分，属于 `sessions.db` 中已持久化的 Marker，不是用户手工操作。

## 3. 事实源与系统边界

`sessions.db` 是对话事实的唯一来源；`akasha.db` 是可以丢弃并重建的派生记忆
状态。

```text
┌──────────────────── sessions.db ────────────────────┐
│ messages / turns / message embeddings / feedback    │
│ markers (akasha_reinforce / akasha_forget)          │
└────────────────────────┬─────────────────────────────┘
                         │ read-only committed turns
                         ▼
┌───────────── Sparse Index (akasha-v2-index.db) ──────┐
│ sparse_turns / turn_dense / turn_terms / feedback    │
│ 持久化 BM25 词典、embedding 身份、因果顺序             │
└────────────────────────┬─────────────────────────────┘
                         │ load_turns() + build_sparse_index()
                         ▼
┌──────────────────── MemoryCycle ─────────────────────┐
│ BurstAwareFeaturePool → residual_push                │
│   → read_pattern_completion (两路)                   │
│     → DynamicMemoryGraph.learn                       │
│       → PlasticityRule + AdaptiveForgetting          │
└────────────────────────┬─────────────────────────────┘
                         │ atomic MemoryUpdate
                         ▼
┌───────────────────── akasha.db ──────────────────────┐
│ turn_nodes / feedback_events / hub_nodes /           │
│ hub_memberships / temporal_edges / plasticity_clock  │
│ / external_seed_state / memory_events / event_seeds  │
│ / burst_context_members / context_state              │
└──────────────────────────────────────────────────────┘
```

Akasha sidecar 保存消息 ID、用于在线读出的 turn 内容、派生特征和图状态。
`sessions.db` 仍是唯一事实源；sidecar 中的 turn 内容只是可重建缓存，不能反向
覆盖源消息。

默认检索池是所有 session 共享的全局池。`MemoryScope` 用来维护当前 session 的
增量上下文和审计来源，而不是把长期记忆硬切成互不相通的 session 子图。调用方
明确提供的时间、kind 等 filter 仍必须在读出边界生效。

## 4. 与论文计算要求的对应

本设计采用论文 *Position: Hippocampal Explicit Memory Is the Cornerstone for
AGI* 的计算术语，但机制是工程实现。

| 论文要求 | Akasha V2 对象 | 实现 |
|---|---|---|
| Sparse Indexing | `BurstAwareFeaturePool`, `FeaturePool` | dense + BM25 + time + context |
| Error-Independent Update | `MemoryCycle`, `DynamicMemoryGraph.learn` | 无监督、无误差信号更新 |
| Associative Construction | `DynamicMemoryGraph`, `Engram`, `TemporalRelation` | 因子化 hub 超边 |
| Pattern Separation | `_tail_surprisal`, channel provenance | 可测量并回归 |
| Pattern Completion | `residual_push`, `read_pattern_completion` | 两路扩散读出 |
| Dynamicity | 增量 `MemoryState` | 每轮提交后在线更新 |
| High and Instant Plasticity | Oja + resource gating | 单次共同激活即可写入 |
| Adaptive Forgetting | 双对数正态 recurrence survival | 基于年龄和独立复现 |
| Message Feedback | `reinforce_feedback_nodes`, `apply_feedback_inhibition` | remember/forget 因果标记 |

论文使用的 dense 表示对应 `DenseRepresentation`。Embedding 模型本身承载跨
样本学习得到的隐式模式。显式记忆负责把稀疏索引、事件关系和原始对话证据绑定。

## 5. 领域语言

### 5.1 核心对象

- `Turn`：全局因果顺序中的一个已提交 user+assistant 实例，携带 dense/BM25
  特征、时间间隙和 `TurnFeedback`；
- `ContextState`：当前 burst 的已完成上下文（归一化成员、pooled dense、pooled
  词条）；
- `SeedEvidence`：sparsemax 后的稀疏激活、通道支持、时间先验、continuation
  信念和 surprise；
- `BurstDecision`：一次 burst 边界判定结果，包含证据、base continuation、
  context dependence、context mass、continued 标志和可见节点；
- `DiffusionResult`：固定点下界 reserve、active_nodes、残差 L1、parent
  provenance；
- `PatternCompletion`：模式补全结果，按来源分类：sharp_completion、
  basin_direct、basin_completion、relative_tail，含活跃 basin 数量；
- `RecallCapture`：将一次查询与其 completion 绑定的持久化记录；
- `PlasticityResult`：一轮学习事件产生的可审计变更；
- `MemoryCycle`：单一因果状态机，在线和重放使用同一 `retrieve → commit` 流程；
- `DynamicMemoryGraph`：存储 hub 超边（engram）、有向 temporal 边和全部
  可塑性状态的领域图。
- `RetrievalTicket`：携带 state_version、prepared_state 和
  prepared_turn_capacity 的可逆检索票据；
- `RetrievalState`：可逆的因果时钟快照，不复制图拓扑。

### 5.2 一轮记忆事件

MemoryCycle 分为两个阶段：

```text
MemoryCycle.retrieve(turn, state_n)
    → RetrievalTicket(state_version=n)

MemoryCycle.commit(turn, ticket, state_latest)
    → CycleCommit + state_(latest+1)
```

retrieve 在临时 advance 的图状态上运行 — grow 容量、prepare 时钟、run
residual_push、run pattern completion — 然后 finally 块恢复为已发布状态。
commit 时正式 advance 并持久化学习结果。同一周期同时用于在线和重放。

## 6. 稀疏索引与 burst 感知

### 6.1 Turn 定义

```text
Turn
├── node_id (全局因果位置)
├── turn_id (稳定的 source message pair identity)
├── session_key
├── user_seq, user_message_id, assistant_message_id
├── user_text, assistant_text
├── user_dense, assistant_dense (已归一化的 embedding 或不完整)
├── user_terms, assistant_terms (Jieba 词条及 tf)
├── started_at, committed_at (UTC)
├── inter_gap_seconds (到前一个全局 turn 的秒数)
└── feedback (TurnFeedback: remember_nodes, forget_nodes, boost)
```

缺少 dense 的 turn 不会跳过；embedding 身份不匹配或恶意向量在边界
fail-loud。

### 6.2 BurstAwareFeaturePool

`BurstAwareFeaturePool` 扩展 `FeaturePool`，维护每个 turn 的因果上下文依赖：

```python
context_dependence[i] = percentile(term_effective_support(turn_i.user_terms),
                                   observed supports before i)
```

即每个 turn 的信息充分性在当前历史中的经验排名。高 rank → 低依赖；短句/歧义
→ 高依赖。

### 6.3 证据融合与 burst 边界

给定当前 query q_t 和可见 burst 上下文：

```text
infer_burst_seed(index, candidate_context, visible_nodes)
    → BurstDecision

1. 分别计算 query 和 context 的 dense + BM25 对历史的 tail surprisal
2. 计算基础 continuation 信念: P(continue | time, sim, surprise)
3. burst_continuation = base + (1-base) * time_prior * context_dependence
4. 若 continued: context_mass = combine_odds(continuation, context_dependence)
5. 最终 seed = mix_sources(query_evidence, context_evidence, context_mass)
```

`_mix_sources` 非固定线性加权：对 query 证据和 context 证据分别做 sparsemax，
然后按 context_mass 插值：

```text
seed[node] = (1 - context_mass) * sparsemax(query)[node]
           + context_mass * sparsemax(context)[node]
```

context_mass 高时（短句/高依赖/已确认继续），context 通道支配 seed；
surprise 高时，query 独走。

### 6.4 证据通道与 surge 检测

四个独立的 tail-surprisal 通道：

```text
query_dense → tail_surprisal(dense_scores(user_embedding, end))
query_bm25  → tail_surprisal(bm25_scores(user_terms, end))
context_dense, context_bm25 → 同上但对 candidate_context
```

Surprise 以 RMS 残差形式计算：

```text
surprise = sqrt(mean([(1 - max(dense_prediction, 0))²,
                      (1 - min(1, max(bm25) / self_score))²]))
```

预测上限是当前 query 在已记住模式中 dense/BM25 的最大匹配度。

### 6.5 中文 BM25

使用 Jieba `LexicalEncoder`，身份记录在 engine metadata：
Jieba 版本、基础词典摘要、自定义词典摘要、normalizer 版本、BM25 统计版本。
线上和重放使用同一编码器。

### 6.6 增量 burst 成员

burst 随 commit 更新：

- continued=True → 追加当前 turn node_id 到 burst_members[ session_key ]
- continued=False → 重新开始为 [ current_turn ]
- 已 inhibited 的节点在构建 context 前即时移除
- ContextState 由 pooled 的归一化 dense + BM25 terms 构成

## 7. 关联构造

### 7.1 Engram 超边

一次共同激活产生因子化 hub:

```text
              Hub H_t
             ╱   │   ╲
          turn a turn c turn f
```

每个 hub（engram）有 O(k) 条 `HubMembership` 双向边。传播时等价为：

```text
W_assoc x = B Λ (B^T x)
```

hub 创建由 `write_gain = evidence.surprise` 缩放，通过 `_integrated_members`
用 factorized Oja 在 1.5-entmax 上投影活性：

```text
membership = entmax15(powered_activity * log1p(turn_count))
```

### 7.2 时间有向关系

同一 burst 内 past → current 获得正向 temporal 边；current → past 获得较弱
反向 temporal 边。倍率由 `reverse_temporal_ratio` 配置。

```text
past ───temporal_forward──▶ current  (weight ∝ continuation * write_gain * activity)
past ◄──temporal_backward── current   (weight = reverse_temporal_ratio * forward)
```

时间距离影响可塑性但不确定"是否建立边"。有效权重 = 存储权重 × retention_factor。

Temporal 边在传播时使用一个独立的 `TemporalGraphView`，只暴露非 membership
边，用于度量时间可达性而不参与关联补偿。

## 8. 模式补全：两路读出

### 8.1 总体架构

读出不再使用单一图扩散，而是两条独立路线：

```text
read_pattern_completion(graph, pool, query, context, evidence, ...)

1. contextual route: full graph diffusion with active burst context
   → 保持 V8 上下文路由的非破坏基线

2. independent (address) route: query-only diffusion without context
   → 仅在 context_dependence < 1.0 时激活
   → address_mass = sparsemax(log[1-dependence, dependence])[0]

3. competitive_route_union(contextual, address, address_mass)
   → 合并 completion 项，独立路由的新增候选经过共享 sparse 竞争
```

这条分为两路的架构解决了短句依赖上下文时无法独立定位目标的问题，同时
不让无关上下文淹没信息充分的 query。

### 8.2 每条 route 的内部结构

每条 route 通过 `_read_route`：

```text
_read_route(graph, query, fields, basin_scores, continuation, ...)

1. _active_basins: 匹配每个 hub 的 raw engram 结构到当前评分
   score = -expm1(-total_membership) * logsumexp(normalized_weights, values)

2. _pooled_heads: 用 temperature scaling 选择 active basin
   temperature = surprise_temperature(surprise, historical_surprise)
                * binary_entropy(continuation)  (仅在非 burst 时)
   用 sparsemax(scores / peak / temperature) 选 basin

3. _accessibility_supported_heads: 筛选 conductance 仍高的 basin
   只保留 head_accessibility = effective_mass / raw_mass 足以通过
   gain_normalized_sparsemax 的

4. _merge_overlapping_heads: 共享 direct seed 坐标的 basin 通过并查集合并
   多个 hub 的 seed 按 mass 加权，输出合并 seed

5. _diffuse_heads: 对每个选中的 basin 独立做两次 residual push
   a. 全图 diffusion → basin_posterior, local_completion
   b. temporal-only diffusion → temporal_posterior
   聚合 sharp, direct, completion, relative_tail 分量

6. relative_tail = entmax15( information * sqrt( temporal ) )
   其中 information = basin * log(2*basin/(basin+sharp)) for basin > sharp
```

### 8.3 带重启的局部扩散 (Residual Push)

图传播使用 `IndexedMaxHeap` 实现的 residual push：

```text
x* = α s + (1-α) P^T x*

每次迭代：
  pop 最大 residual 节点 v
  reserve[v] += α * residual[v]
  对每个出邻居 u: residual[u] += (1-α) * residual[v] * P[v→u]
  剩余未传播质量回到 seed 方向防止泄露
```

停止条件：`L1(residual) ≤ tolerance`。每次中间结果满足 `reserve ≤ x*`。

`DynamicMemoryGraph.transitions()` 返回每节点的归一化 outward 质量，使用
`-expm1(-total_weight)` 的饱和 spread——防止总质量超出 1.0。

传播路径可通过 `parent_node` / `parent_edge` arrays 追踪，用于主导路径审计。

### 8.4 读出与来源分类

两类扩散分量：

```text
sharp_completion: sharp seed 扩散后，seed 节点之外的 completion
basin_direct:     在 basin seed 中直接命中
basin_completion: basin 扩散但不在 seed 中的节点
relative_tail:    entmax15 尾部保留的相对增益且时间可达
```

最终 `_competitive_route_union` 合并两条路的 item：
- contextual 的所有 item 保留
- address 的 `basin_completion` 项与 contextual 的 score 一起做一次共享
  sparsemax 竞争（除以各自峰值），仅通过的候选才加入最终集
- 可见节点（当前 burst 内的）从最终集中排除

去重、score 取 max、来源取并集。

## 9. 可塑性、抑制与遗忘

### 9.1 Oja 风格学习与资源门控

每条边在暴露于活性时经历 Oja 规则更新，由一个资源门控的 eligibility 调制：

```text
Δw = η · eligibility(resource, threshold, activity)
    · hub_activity · (member_activity - hub_activity · w)
```

其中 eligibility 是三个因子的乘积：

```text
resource:  指数恢复 1 - (1-resource) * exp(-elapsed / resource_tau)
threshold: 指数下降 threshold * exp(-elapsed / threshold_tau)
drive:     activity * max(activity - threshold, 0)
eligibility = resource * -expm1(-drive)
```

刺激后资源消耗 `resource *= exp(-activity)`，阈值提升
`threshold += (1-threshold) * -expm1(-activity²)`。未受刺激的边资源
逐步恢复、阈值逐步下降。

### 9.2 有限连接预算

- 每个 hub 的 membership 总权重 ≤ `recurrent_budget`
- 每个 source turn 的所有 membership 出边总权重 ≤ `recurrent_budget`
- temporal forward 源 ≤ `recurrent_budget`
- temporal backward 源 ≤ `reverse_temporal_ratio * recurrent_budget`

归一化是比例缩放的，包含抑制量。增强某些边 → 同源其他边归一化后缩小。

### 9.3 双对数正态自适应遗忘

常规访问时不批量改写全图。有效权重在每次访问时计算：

```text
w_effective = w_stored * retention_factor(edge_id)
retention_factor = recurrence_survival(age)
  where age = elapsed_seconds - last_support_seconds
```

`recurrence_survival` 使用在线 Welford 维护的双组件对数正态模型：

```text
观察到的每个外部 seed 复现事件 -> _observe_recurrence(gap_seconds, credit)
  → 按 log-distance 分配到 short 或 long 组件
  → 在线更新 mean, M2, weight

survival(age) = Σ weight_i/Σweight · 0.5 · erfc( (ln(age) - μ_i) / (√2 · σ_i) )
```

`_support_edge` 部分更新 `last_support_seconds`：

```text
renewal = -expm1(-credit)
last_support = previous + renewal * spacing
```

这样每个变激活的边适度延缓了老化时钟，但不等于完全重置。

### 9.4 独立、观察与回放信用

学习信号分为三类信用：

- **observed_credit**: 当前 seed 直接命中的节点获得的 credit
- **recurrent_credit**: 通过图扩散激活但与当前 seed 无关
- **independent_credit**: 当前外部 seed 的几何平均 `√(source_external · target_external)`

`_support_edge` 分别接收 support_credit（几何平均 `√(member_activity · hub_activity)`）
和 independent_credit。

### 9.5 Message Feedback: 记忆与抑制

Message feedback 以从 `sessions.db` 引入的 `TurnFeedback` 形式到达每个 turn：

```text
TurnFeedback(remember_nodes, forget_nodes, remember_boost)
```

这是持久化在 source 中的因果 Marker，不是运行时 API。MemoryCycle 的 commit
阶段在每个 turn 写入前应用反馈：

```text
inhibited_nodes.update(forget_nodes)
inhibited_nodes.difference_update(remember_nodes)
graph.apply_feedback_inhibition(inhibited_nodes)
graph.learn(event, evidence, diffusion)
graph.reinforce_feedback_nodes(remember_nodes, boost)
```

**Feedback inhibition** (`apply_feedback_inhibition`):
从 `current_external` 中移除被抑制的 turn——阻止它们在当轮获得独立可塑性支持，
但不删除图拓扑。

**Feedback reinforcement** (`reinforce_feedback_nodes`):
在 log-weight 空间增强目标 turn 在自身 episode 的 membership：

```text
gain = boost ** learning_rate
w_target = min(1.0, w * gain)
```

随后重新归一化受影响的 hub 和 source 的传导预算。强化只作用于自身 episode，
不会给语义簇或 query seed 追加奖励。

**During readout**, `_exclude_recall_items` 在完成计算后从最终结果中移除
inhibited 节点——它们仍参与图扩散，但不在用户可见的回忆结果中出现。

### 9.6 Interaction of forgetting and feedback

forget 保持节点在图中作为联想桥，但移除：
1. 最终召回中的可见性
2. 后续独立可塑性支持
3. burst context 中的包含

相关语义簇因后续因果学习可能产生可见变化——这是保留联想传播的预期 tradeoff，
不是 bug。

## 10. 在线闭环

### 10.1 查询阶段

```text
AkashaMemoryEngine.query
  │
  ├─ 校验 query / timestamp / embedding 身份
  ├─ embed 用户文本 → 归一化向量
  ├─ async commit_gate + event loop synchronization
  ├─ OnlineMemoryRuntime.query_turn
  │   └─ MemoryCycle.retrieve (在可逆读帧内)
  ├─ engine._records: 生成 dense lane + completion lane
  │   │
  │   ├─ dense lane: ≤5 项纯 dense 相似度 (direct_dense)
  │   ├─ completion lane: Ticket completion items 去重后取 ≤context_recall_limit 项
  │   └─ 按时间排序展示
  ├─ 若 intent=context + effect=stateful: 保留 PendingRetrieval
  ├─ 若 intent=context: 生成 context block
  └─ 返回 MemoryQueryResult
```

`effect=read_only` 不写 ticket、不更新可塑性、不改变上下文。

### 10.2 提交阶段 (两阶段 staging)

```text
TurnCommitted
  │
  ├─ 排除 scheduler session / skip_post_memory
  ├─ 嵌入 user + assistant 真实文本 (重用/重新调用 embedding API)
  │   若有匹配的 pending cue → 重用 query embedding
  ├─ upsert 到 MessageEmbeddingStore
  │
  ├─ [stage] OnlineMemoryRuntime.stage_from_source
  │   ├─ build_sparse_index → 增量持久化新 turn
  │   ├─ load_turns → 验证新 suffix
  │   ├─ 返回 StagedOnlineCommit
  │   └─ 异步 publish
  │
  └─ [publish] OnlineMemoryRuntime.publish_staged
      ├─ 获取 state_lock
      ├─ 对每个 suffix turn 执行 cycle.retrieve + cycle.commit
      ├─ write_memory_database → 原子 SQLite 快照
      ├─ 失败时从持久化 snapshot 恢复 cycle
      └─ 释放 stale pending
```

retrieve 产生的 `RetrievalTicket` 携带 `prepared_turn_capacity` 和
`prepared_state`，使 commit 可以精确恢复 read frame。若 ticket 丢失/陈旧，
commit 在最新 state 上重新 evaluate。

### 10.3 双向通道读出

`engine._records` 生成两个独立的 lane：

```text
RetrievalRecords
├── dense (tuple):     direct_dense lane ≤5 项
└── completion (tuple): pattern completion lane
    ├── sharp_completion: seed 扩散的图完成项
    ├── basin_direct:      basin seed 直接命中
    ├── basin_completion:  basin 扩散的图完成项
    └── relative_tail:    entmax15 尾部稀有项
```

dense lane 和 completion lane 按 node_id 去重（dense 优先）。
`strong` 相关性过滤排除纯 relative_tail。

### 10.4 原子事务

一轮事务同时提交：

1. `turn_nodes` 含幂等记录（重复 turn_id → 校验 → 确认，不覆盖）
2. `feedback_events` 含因果 Marker 输入
3. hub_nodes + hub_memberships + temporal_edges + plasticity_clock + external_seed_state
4. memory_events + event_seeds + event_channel_support + event_channels
5. burst_context_members + context_state
6. recall_runs + recall_items

任一步失败，整轮回滚到上一个持久化 snapshot。

## 11. 重放与重建

在线与重放使用同一个 `MemoryCycle`：

```text
在线：
AkashaMemoryEngine.query → OnlineMemoryRuntime.query_turn → MemoryCycle.retrieve
TurnCommitted           → OnlineMemoryRuntime.stage_from_source + publish_staged
                           → MemoryCycle.commit

重放：
rebuild_memory
  → load_turns(index_path) → BurstAwareFeaturePool
  → for turn: cycle.retrieve → cycle.commit
  → write_memory_database
```

预检全部通过前不能清空或替换目标库。embedding 非法、身份不完整或数据库损坏
均以非零退出码失败。

## 12. 确定性

全局 turn 顺序固定为：

```text
started_at UTC → session_key UTF-8 → user_seq → turn_id UTF-8
```

实现必须满足确定性要求列在 §12 原文中，并补充：

- `BurstAwareFeaturePool.context_dependence` 使用确定性 order statistics
- `_IndexedMaxHeap` 以稳定二级键（node ID）平局
- `entmax` / `entmax15` / `sparsemax` 均以 `np.flatnonzero` + 稳定排序
- feedback marker 解析在 `load_turns` 时确定性地将 turn targets 转换为 node IDs
- engine identity string: `"single_state_empirical_recurrence_survival_v9_feedback"`

## 13. 持久化模型

### 13.1 引擎版本

```text
engine: "single_state_empirical_recurrence_survival_v9_feedback"
```

Schema user_version = 2。

### 13.2 表职责

| 表 | 职责 |
|---|---|
| `metadata` | schema、算法、embedding、tokenizer、配置身份、graph 容量 |
| `turn_nodes` | turn、真实 message IDs、时间间隙 |
| `feedback_events` | 当轮 remember/forget 的因果 marker 输入 |
| `hub_nodes` | 因子化 engram 头、创建事件、阈值、innovation mass |
| `hub_memberships` | 每 membership 边的 weight、effective_weight、observed/recurrent/support/independent credit、resource、plasticity_threshold、last_support/stimulated_seconds |
| `temporal_edges` | 正/反向时间关系（和 membership 相同的可塑性列） |
| `plasticity_clock` | 单行：elapsed_seconds、short/long gap 和 recurrence 统计、resource/threshold/retention tau |
| `external_seed_state` | 每节点的最后外部 seed 时间 |
| `memory_events` | 每轮 retrieve/commit 事件、时间先验、continuation、surprise、seed 大小、质量分解 |
| `event_seeds` / `event_channel_support` / `event_channels` | seed 坐标、通道支持、事件级通道 |
| `activation_runs` / `activation_items` | target 事件的 reserve、completion、graph-only completion、dominant path |
| `recall_runs` / `recall_items` | 模式补全结果，按来源分解计数 |
| `burst_context_members` / `context_state` | 各 session 当前 burst 与累计上下文 |

### 13.3 幂等

相同 `turn_id` 再次提交：
- 消息 ID、source digest 和算法身份一致：确认已处理，不重复学习
- 任一身份不同：抛出冲突错误

## 14. Akasic Agent 插件契约

### 14.1 插件构造

`memory_plugin.py` → `AkashaMemoryEngine`：
- plugin_id = "akasha"
- 暴露 engine、admin、embedding API 和 closeables

### 14.2 `MemoryEngine` 方法

| 接口 | 行为 |
|---|---|
| `query` | context/answer/interest/procedure 使用两路显式模式补全；timeline 明确 unsupported；输出 dense + completion 双 lane |
| `ingest` | 仅接受带稳定 message IDs 的 `conversation_turn`；主入口由 `TurnCommitted` adapter 调用 |
| `mutate` | remember/forget 返回 `accepted=False, status=unsupported` — 不使用 admin API |
| `reinforce_items_batch` | 明确的确定性 no-op |
| `describe` | 声明 RICH_MEMORY_ENGINE + 6 项能力 |
| `tool_profile` | 公开 recall_memory + remember_memory + forget_memory 三项工具 |
| `stage_feedback` / `take_staged_feedback` | Agent 调用的 Message 级反馈：解析 Message ID → turn ID，与 current_turn 绑定，通过 AkashaFeedbackPersistModule 持久化 |
| `keyword_match_procedures` | 返回空列表 |
| 时间事件与 dashboard 读取 | 查询 memory_events、bindings、engrams 和 trace |
| dashboard update/delete | 抛出明确的 unsupported operation |

### 14.3 返回映射

每个完成 turn 映射为一个 `MemoryRecord`：

```text
MemoryRecord
├── id: stable turn ID
├── kind: "episodic_turn"
├── summary: user + assistant 摘要
├── score: 读出质量 (dense cos 或 completion score)
├── engine_kind: "akasha"
├── evidence
│   └── EvidenceRef(kind="message_range", refs=[user_id, assistant_id])
├── signals
│   ├── lane: "dense" | "completion"
│   ├── sources: ["sharp_completion" | "basin_direct" | "basin_completion" | "relative_tail" | "direct_dense"]
│   ├── basin_ids: 促成召回的 hub ID 列表
│   ├── started_at, user_text, assistant_preview
│   └── also_completed: 该 dual-lane item 是否也出现在另一条 lane
└── injected: 是否用于 context 注入
```

text_block 只在 `intent=context` 时生成，分"左脑记忆（精确回忆）"和
"右脑联想（潜意识第一反应）"两部分，受 `inject_max_chars` 预算控制。

## 15. 代码边界

```text
akasha-v2-engine/src/akasha/
├── domain/
│   ├── model.py       # Turn, ContextState, SeedEvidence, DiffusionResult, PlasticityResult, Capture, TurnFeedback, MemoryConfig
│   ├── features.py    # FeaturePool, BurstAwareFeaturePool, BurstDecision, BM25, burst logic, sparsemax, entmax
│   ├── graph.py       # DynamicMemoryGraph (hubs, edges, plasticity, recurrence), RetrievalState
│   ├── diffusion.py   # residual_push, IndexedMaxHeap, TransitionGraph protocol
│   └── readout.py     # read_pattern_completion, two-route architecture, basin/head selection, entmax variants
├── application/
│   ├── cycle.py       # MemoryCycle (retrieve + commit), RetrievalTicket, CycleCommit
│   ├── runtime.py     # OnlineMemoryRuntime (stage + publish, crash recovery)
│   └── rebuild.py     # rebuild_memory, deterministic_metadata
├── infrastructure/
│   ├── loader.py      # load_turns from sparse index
│   ├── persistence.py # write_memory_database / load_memory_state, SQLite schema
│   ├── lease.py       # writer lease
│   └── sparse_index/  # build_sparse_index, encoding (Jieba), model, schema
├── engine.py          # AkashaMemoryEngine (Akasic Agent adapter), feedback staging
├── memory_plugin.py   # MemoryPlugin factory
├── config.py          # AkashaConfig load/validate/render
├── inspector.py       # read-only retrieval projection
├── dashboard.py / dashboard_panel_inspector.{ts,css} / mobile_ui.{js,css}
└── cli.py
```

依赖方向：

```text
Akasic Agent adapter (engine.py, memory_plugin.py)
         │
         ▼
application ─────────▶ domain
     │                    ▲
     ▼                    │
application ports ◀── infrastructure
```

domain 不导入 SQLite、Akasic Agent 或网络客户端。

## 16. 错误处理

信任边界：
- Akasic Agent `MemoryQuery` / `MemoryIngestRequest` / `TurnCommitted`
- config 加载
- sessions/akasha/index SQLite 读取
- embedding 响应
- feedback marker 解析
- rebuild CLI

必须 fail-fast、fail-loud 的情况同 §16 原文，补充：
- feedback remember/forget 节点不在因果范围内
- inhibited nodes 与 remembered nodes 在同一 turn 中重叠
- 图容量在 commit 时收缩
- 多个 staged commit 同时 publish

## 17. 并发和恢复

- 图写入采用单写者语义（`WriterLease`）
- query 从 reversible read frame 读取（grow → prepare → process → restore）
- SQLite 事务序列化 `MemoryUpdate`
- 进程崩溃时未提交事务自动回滚
- 启动时扫描事实源中晚于最新 `processed_turns` 的 committed turn
- 缺 ticket 的 turn 重温 cause 并标记 `retrieval_recomputed=true`
- 在线 publish 失败时从 `akasha.db` 持久化 prefix 恢复 `MemoryCycle`
- 关闭时撤销 event 订阅、drain 异步 publication、回收所有数据库资源
- feedback marker 在 staging 阶段即持久化到 sparse index，publish 阶段应用

`RetrievalState` 和 `RetrievalTicket.prepared_state` 使 retrieval 可以回滚
弹性时钟（elapsed_seconds、recurrence stat 等）而不需要复制整个图。

## 18. 验证与验收

（与原文一致，补充完整）

### 18.1 单元测试

覆盖：
- 证据融合、sparsemax / entmax15 / entmax 的稳定 tie-break
- 增量 BM25 与批量重算一致
- burst 只使用过去信息
- context_dependence 为经验排名不变性
- residual push 的 reserve 下界、残差误差和质量守恒
- engram 因子化传播与概念矩阵等价
- 时间关系方向性、TemporalGraphView 的正确性
- 连接预算阻止重复输入无限增长
- 资源消耗、恢复和阈值调节
- 双对数正态 recurrence survival 的统计特性
- feedback remember/forget 因果验证和边界
- inhibited nodes 在图传播中完整、在召回结果中隐藏
- 不同 `PYTHONHASHSEED` 的确定性

### 18.2 合成机制实验

（与原文 §18.2 一致，补充 feedback 相关测试）

7. remember 增强 target turn 在自身 episode 的 membership 而不增加总预算
8. forget 隐藏 target turn 的召回同时保留联通性桥接
9. 冲突反馈 (same turn 记住又忘记同一目标) 在边界拒绝

### 18.3–18.6

V8 私有 parity、在线-重放等价、故障测试、性能要求与原文一致。

## 19. 论文要求的可证伪边界

（与原文一致）

## 20. 交付边界

（与原文一致，补充）
- 实现 feedback staging + AkashaFeedbackPersistModule 集成
- 通过 feedback marker 的因果重建验证

## 21. 参考资料

1. *Position: Hippocampal Explicit Memory Is the Cornerstone for AGI*,
   arXiv:2606.11245
2. *RF-Mem*, arXiv:2605.05097
3. Andersen, Chung, Lang, *Local Graph Partitioning using PageRank Vectors*,
   FOCS 2006
4. Oja, *Simplified neuron model as a principal component analyzer*,
   Journal of Mathematical Biology, 1982
5. Bi and Poo, *Synaptic Modifications in Cultured Hippocampal Neurons*,
   Journal of Neuroscience, 1998
6. Peters, Niculae, Martins, *Sparse Sequence-to-Sequence Models*,
   ACL 2019 (entmax 系列)
