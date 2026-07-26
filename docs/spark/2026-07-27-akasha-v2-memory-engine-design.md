# Akasha V2 显式记忆引擎设计规格

- 状态：已实现并通过首版验收
- 日期：2026-07-27
- 目标仓库：`/mnt/data/coding/akasha-v2-engine`
- 目标插件 ID：`akasha`
- 设计基线：`/mnt/data/coding/akasha-v2` 的稳定 V8 机制与冻结评估结果

## 1. 目标

Akasha V2 是一个独立、可在线运行、可确定性重放的显式记忆引擎。它把每轮
`user + assistant` 对话编码成稀疏索引，通过共同激活、时间关系、局部扩散、
可塑性和自适应遗忘形成可持续生长的关联记忆。

首版必须同时满足：

1. 召回效果与冻结的 Akasha V8 基线一致；
2. 线上逐轮写入与离线按相同历史重放得到等价领域状态；
3. 符合 Akasic Agent 当前 `MemoryEngine` 和 `MemoryPlugin` 接口；
4. 所有学习来自对话、检索和历史复现，不依赖用户标签、外部确认或
   fast/slow/tag/reinforce 双轨机制；
5. 数据库可以由 `sessions.db`、消息 embedding、算法版本和配置确定性重建；
6. 代码可以作为整个 `plugins/akasha` 的替代来源，但本项目不负责修改或部署
   Akasic Agent；
7. 仓库最终初始化为独立 Git 仓库并发布为公开 GitHub 仓库
   `akasha-v2-engine`。

## 2. 非目标

首版明确不做：

- 修改 `/mnt/data/coding/akasic-agent`；
- 迁移旧 Akasha sidecar schema；
- 训练或微调 embedding 模型；
- 把脑区名称直接映射成 Python 类；
- 单独返回 direct-dense 或 direct-BM25 结果通道；
- 用户手工 remember、forget、tag 或 reinforce；
- 全局 Hopfield 多吸引子动力学；
- 后台物理删除和压缩陈旧节点；
- 声称系统已经完整复现海马体或证明了 AGI。

Dense 和 BM25 在首版中是构造稀疏 seed 的证据。返回结果仍以 V8 的显式模式
补全读出为准，避免加入新结果通道后破坏冻结效果基线。

## 3. 事实源与系统边界

`sessions.db` 是对话事实的唯一来源；`akasha.db` 是可以丢弃并重建的派生记忆
状态。

```text
┌──────────────────── sessions.db ────────────────────┐
│ messages / turns / message embeddings               │
│ user 与 assistant 原文、稳定 ID、真实提交顺序         │
└────────────────────────┬─────────────────────────────┘
                         │ read-only committed turns
                         ▼
┌──────────────────── MemoryCycle ─────────────────────┐
│ SparseIndexer → PatternCompleter → IndexReadout      │
│                         │                            │
│                         └→ PlasticityRule            │
│                              + AdaptiveForgetting    │
└────────────────────────┬─────────────────────────────┘
                         │ atomic MemoryUpdate
                         ▼
┌───────────────────── akasha.db ──────────────────────┐
│ bindings / features / engrams / temporal relations  │
│ plasticity / context / retrieval and learning trace │
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
AGI* 的计算术语，但机制是工程实现，不把论文中的生物学类比当成已验证事实。

| 论文要求 | Akasha V2 对象 | 首版承诺 |
|---|---|---|
| Sparse Indexing | `SparseIndexer`, `SparseIndex` | 完整实现 |
| Error-Independent Update | `MemoryCycle`, `MemoryUpdate` | 无监督、无误差信号更新 |
| Associative Construction | `AssociativeMemory`, `Engram`, `TemporalRelation` | 完整实现 |
| Pattern Separation | `PatternSeparationTrace` | 可测量并回归，不声称等价于齿状回 |
| Pattern Completion | `PatternCompleter`, `PatternCompletion` | 局部残差扩散实现 |
| Dynamicity | 增量 `MemoryState` | 每轮提交后在线更新 |
| High and Instant Plasticity | `PlasticityRule` | 单次共同激活即可写入 |
| Adaptive Forgetting | `AdaptiveForgetting` | 基于年龄和经验复现的有效强度 |

论文使用的 dense 表示对应 `DenseRepresentation`。Embedding 模型本身承载跨
样本学习得到的隐式模式；Akasha 不把它误称为本引擎训练出来的记忆。显式记忆
负责把稀疏索引、事件关系和原始对话证据绑定起来。

## 5. 领域语言

### 5.1 核心对象

- `DenseRepresentation`：query 或 turn 的 embedding 表示；
- `SparseIndexEvidence`：dense、BM25、上下文、时间和经验先验证据；
- `SparseIndex`：少量带权激活项组成的稀疏编码；
- `IndexBinding`：稀疏索引项与真实 turn/message ID 的绑定；
- `Engram`：一次共同激活模式的因子化超边；
- `TemporalRelation`：过去与当前之间的有向时间关系；
- `MemoryState`：当前 bindings、engrams、关系、可塑性和上下文状态；
- `PatternCompletion`：给定 partial index 后的稳定下界、残差和来源；
- `MemoryUpdate`：一轮提交产生的原子领域变更；
- `RecallResult`：提供给 Akasic Agent 的完成结果与审计轨迹。

不使用 `DentateGyrus`、`CA3Neuron` 等脑区名称。代码名称表达计算职责，而不是
假定工程模块与脑区一一对应。

### 5.2 一轮记忆事件

论文形式：

\[
f_{\text{memory}}(E_t, M_t) \rightarrow (\Delta M_t, Y_t)
\]

Akasha 的在线现实需要先给 LLM 返回记忆、等 assistant 落库后再学习，因此一个
`MemoryCycle` 分为两个阶段：

```text
MemoryCycle.retrieve(cue, state_n)
    → RecallResult + RetrievalTicket(state_version=n)

MemoryCycle.commit(committed_turn, ticket, state_latest)
    → MemoryUpdate + state_(latest+1)
```

这两个阶段属于同一领域过程，不能分别实现成线上算法和重放算法。

## 6. 稀疏索引

### 6.1 Turn 定义

一个可学习节点必须对应一个已经提交的逻辑 turn：

```text
Turn
├── turn_id
├── session_key
├── user_message_id
├── assistant_message_id
├── user_text
├── assistant_text
├── user_embedding
├── assistant_embedding
├── started_at / completed_at
└── stable source digest
```

首版只把已经提交且相邻的 `user + assistant` 消息组成 turn，不构造假消息补齐。

### 6.2 证据组成

给定当前 query \(q_t\)，`SparseIndexer` 计算：

\[
E_t =
\{
e_{\text{query-dense}},
e_{\text{query-bm25}},
e_{\text{context-dense}},
e_{\text{context-bm25}},
e_{\text{temporal}},
e_{\text{continuation}},
e_{\text{surprise}}
\}
\]

其中：

- query dense 和 BM25 提供当前内容的直接证据；
- context dense 和 BM25 来自当前 session 已结算的前簇状态；
- temporal evidence 来自增量时间模型，而不是固定“前 N 条”；
- continuation 表示当前 query 延续现有 burst 的后验倾向；
- surprise 衡量 query 相对于全局历史和当前 burst 的新颖程度。

融合不采用固定线性加权。V8 的可靠性调制、信息量调制和 sparsemax 稀疏化是
冻结行为的一部分：

- 当前 query 信息充分、惊喜度高时，context 证据自动减弱；
- 当前 query 短、歧义高或信息量低时，context 证据增强；
- 重复定时任务因全局惊喜度低而不能持续垄断 seed；
- 所有证据仍保留独立 provenance，不能先合成一个不可解释的分数。

`SparseIndex` 是融合后的部分激活，不是仅携带多种字段的结构体。每个激活项
必须记录融合前证据、融合后质量和选择责任。

### 6.3 中文 BM25

首版采用固定版本的 Jieba lexical encoder，并把以下身份写入
`engine_metadata`：

- Jieba 包版本；
- 基础词典摘要；
- 自定义词典摘要；
- normalizer 版本；
- BM25 统计版本。

线上和重放必须调用同一个 `LexicalEncoder`。依赖、词典或身份不匹配时失败，
不能退回逐字切分、空 token 或另一套 FTS 实现。

### 6.4 增量 burst

burst 是随历史更新的连续状态，不是预先切好的 session 片段。当前 turn 只能看
过去：

\[
p_t(\text{continue})
=
g(
\Delta t,
\operatorname{sim}(q_t,C_{t-1}),
\operatorname{surprise}(q_t),
\operatorname{uncertainty}(q_t)
)
\]

`ContextState` 保存整个当前 burst 的结算表示，不只保存上一轮。发生明显 topic
drift 时，新 query 的自身证据主导；短句或省略句则可沿同 burst 的累计上下文
找到入口。burst 影响 seed 和时间关联，但同窗口内容不需要重复召回给 LLM。

## 7. 关联构造

### 7.1 Engram 超边

一次共同激活模式 \(b_t\) 概念上产生：

\[
\Delta W_t = \eta_t b_t b_t^\top
\]

实际不展开成 \(O(k^2)\) clique，而保存一个 `Engram` 和 \(O(k)\) 条
`EngramMembership`：

```text
              Engram H_t
             ╱    │    ╲
          turn a turn c turn f
```

传播时等价为：

\[
W_{\text{assoc}}x = B\Lambda(B^\top x)
\]

Engram 表示“这些索引曾在同一认知事件中共同激活”，不是人工主题标签。

### 7.2 时间有向关系

同一 burst 内，过去 turn 到当前 turn 获得较强正向关系；当前到过去获得较弱
反向关系：

```text
past ───────────────▶ current
     strong forward

past ◀─────────────── current
     weaker recall
```

具体倍率属于冻结算法配置并参与 `algorithm_identity`，不得散落成代码常量。
时间距离形成连续增益，不通过固定窗口决定“有边/无边”。

Engram 负责情景模式补全，时间关系负责顺序、前因和链式传播。二者不能混成一类
边，否则无法解释关联来源。

## 8. 模式补全

### 8.1 转移图

传播图由非负关系组合：

\[
A =
\lambda_E A_E
+ \lambda_F A_T
+ \lambda_B A_T^\top
\]

按节点出边归一化得到行随机矩阵 \(P\)。连接预算使新增强关系占用有限质量，
形成局部竞争，而不是让总强度无限增长。

### 8.2 带重启的局部扩散

给定 seed \(s\)，模式补全固定点为：

\[
x^* = \alpha s + (1-\alpha)P^\top x^*
\]

使用 residual push 求局部近似，维护：

- `reserve`：已经结算的稳态激活下界；
- `residual`：尚未展开的路径质量；
- `provenance`：质量经过的 engram 和 temporal relation。

停止条件是：

\[
\lVert residual \rVert_1 \le \varepsilon
\]

而不是固定 hop 或固定 top-k。每次中间结果满足：

\[
reserve \le x^*
\]

并且剩余绝对误差由 residual 质量界定。循环只能积累成稳态，不能无限放大。

### 8.3 读出

`IndexReadout` 把结算激活聚合成 V8 的 basin completion 结果：

1. 按共享 engram、时间路径和可访问质量形成局部 basin；
2. 用 accessibility 与局部 entmax 尾部保留相关 storyline；
3. 依据 residual 误差界和调用方输出预算停止；
4. 对相同 turn、相同 user/assistant message pair 去重；
5. 通过 `IndexBinding` 解析真实消息。

模式补全内部不以固定候选数量为收敛条件。`MemoryQuery.limit` 和 LLM context
预算是输出边界，不反过来改变图动力学。首版不额外混入独立 direct-dense 或
direct-BM25 返回项。

## 9. 可塑性、抑制与遗忘

### 9.1 激活即学习

一轮已提交 turn 的学习信号来自：

- 当前 query 产生的直接 seed；
- 模式补全结算激活；
- 当前 turn 自身；
- 当前 burst 的时间邻近关系；
- 该模式在不同事件中的独立复现。

没有 fast/slow memory、tag、用户确认或显式 reinforce。被检索到的节点会影响
学习，但不会把全部召回质量原样写回，从而避免纯图循环自我证明。

### 9.2 竞争和有限资源

`PlasticityRule` 保留 V8 已验证的机制：

- Oja 风格的归一化竞争；
- 每个节点或关系的有限连接预算；
- 刺激后的资源消耗与随时间恢复；
- 随强刺激提高的可塑性阈值；
- observed、independent 与 recurrent credit 分离；
- 重复激活的边际增益递减。

因此，关联可以在第一次共同激活时形成，但同一条重复 query 不能无限强化同一
模式。增强某些连接时，其他连接在归一化预算中的份额自然受抑制。

### 9.3 自适应遗忘

常规读取不批量改写全图，而在访问时计算：

\[
w_{\text{effective}}(t)
=
w_{\text{stored}}
\cdot
S_{\text{recurrence}}
(
\Delta t,
n_{\text{independent}},
n_{\text{observed}},
n_{\text{recurrent}}
)
\]

真实、跨事件复现提高生存性；仅在一次 burst 内偶然共同激活、以后不再复现的
节点逐渐失去有效质量。

```text
第一次：query f → {a, c, e}
第二次：query g → {a, b, c}
后续：  a/b/c 独立复现，e 不再出现

结果：  a/b/c 的有效支持恢复并稳定
        e 的 membership 与访问质量随时间下降
```

召回会恢复部分可访问性，但恢复量受独立证据、资源和可塑性阈值限制。这样既不
是单调只忘不激活，也不会让一次误召回永久存活。

常规在线流程不物理删除节点。物理压缩属于单独维护能力，不进入首版关键路径。

## 10. 在线闭环

### 10.1 查询阶段

```text
MemoryEngine.query
  │
  ├─ 校验 query / timestamp / embedding identity
  ├─ 读取 MemoryState(version=n) 的不可变快照
  ├─ SparseIndexer.encode
  ├─ PatternCompleter.complete
  ├─ IndexReadout.read
  ├─ 返回 RecallResult 给 LLM
  └─ effect=stateful 时持久化 RetrievalTicket
```

`effect=read_only` 不写 ticket、不更新可塑性、不改变上下文，用于评估和管理
查询。`timeline` 首版明确返回 unsupported trace；不能伪装成空的成功时间线。

### 10.2 提交阶段

插件订阅 `TurnCommitted`，取得稳定 `turn_id`、user/assistant message ID 和
timestamp：

```text
TurnCommitted
  │
  ├─ 从 sessions.db 读取并验证真实消息
  ├─ 取得或生成同模型 embedding
  ├─ 关联 RetrievalTicket
  ├─ 检查 ticket.state_version
  ├─ 必要时在最新 MemoryState 上重新检索
  └─ 一个 SQLite 事务提交 MemoryUpdate
```

如果 query 返回后图已经变化，commit 必须在最新版本重新计算学习状态，并在
trace 中同时保存 served version 和 learned version。不能把过期 ticket 静默写入
新图。

若 turn 没有对应 ticket，仍可用该 turn 的 user cue 在最新状态执行同一
`MemoryCycle.retrieve`，再提交；这覆盖进程重启后的恢复，但必须在 trace 标记
`retrieval_recomputed=true`。

### 10.3 原子事务

一轮事务同时提交：

1. `processed_turns` 幂等记录；
2. `IndexBinding` 和派生 turn feature；
3. BM25 增量统计；
4. memory event 与 sparse activation；
5. engram 和 memberships；
6. temporal relations；
7. plasticity 与 forgetting 状态；
8. session burst/context 状态；
9. retrieval/learning trace；
10. `state_version + 1`。

任一步失败，整轮回滚。

## 11. 重放与重建

重放不是另一套算法：

```text
在线：
query              → MemoryCycle.retrieve
TurnCommitted      → MemoryCycle.commit

重放：
CommittedTurnSource.iter_turns
                   → MemoryCycle.retrieve
                   → MemoryCycle.commit
```

重建脚本 UX 参考旧 Akasha：

```text
python scripts/rebuild_akasha.py \
  --config config.toml \
  --sessions-db /path/to/sessions.db \
  --db-path /path/to/akasha.db
```

执行顺序：

```text
预检 sessions.db / embedding / tokenizer / config
  │
创建临时 akasha.db
  │
按确定性全局顺序重放所有 committed turn
  │
校验 schema、不变量、逻辑 hash 和冻结 query suite
  │
备份已有目标库
  │
原子替换 akasha.db
```

预检全部通过前不能清空或替换目标库。缺少 dense 的完整 turn 仍以 BM25 和
时间证据入库并记录计数；已有 embedding 非法、模型不一致、消息身份不完整或
数据库损坏均以非零退出码失败，不允许跳过。

## 12. 确定性

全局 turn 顺序固定为：

```text
started_at UTC
→ session_key 的 UTF-8 bytes
→ user_seq
→ turn_id
```

实现必须满足：

- 所有 SQLite 查询显式 `ORDER BY`；
- set 和 dict 进入算法前转换成稳定排序序列；
- priority queue 使用稳定二级键；
- 浮点聚合顺序固定；
- NumPy dtype 明确；
- 并列候选按稳定 turn ID 解决；
- 随机机制如果保留，必须由持久化 seed 驱动；
- Python `PYTHONHASHSEED` 不影响结果；
- `algorithm_identity` 包含算法版本、图参数、embedding、tokenizer、词典和
  feature schema；
- 逻辑状态 hash 基于 canonical serialization，不基于 SQLite 文件字节或 rowid。

相同 `sessions.db` 快照、embedding、配置和代码版本必须得到相同逻辑状态 hash。

## 13. 持久化模型

### 13.1 表职责

| 表 | 职责 |
|---|---|
| `metadata` | schema、算法、embedding、tokenizer、配置身份 |
| `turn_nodes` | turn、真实 message IDs、文本缓存和全局顺序 |
| `memory_events` | 每轮无监督 retrieve/commit 事件 |
| `event_seeds` / `event_channels` | seed 及各证据通道 |
| `activation_runs` / `activation_items` | reserve、residual 和激活下界 |
| `hub_nodes` / `hub_memberships` | 因子化 engram 与可塑性状态 |
| `temporal_edges` | 正反向时间关系与可塑性状态 |
| `plasticity_clock` / `external_seed_state` | 复现时间尺度与外部 seed 时钟 |
| `burst_context_members` / `context_state` | 各 session 当前 burst 与累计上下文 |
| `recall_runs` / `recall_items` | 模式补全结果和 provenance |

独立稀疏索引中的 `sparse_turns`、`turn_dense`、`turn_terms`、lexical/time
statistics 都是性能缓存，不是事实源。索引保存 source digest 和 feature
identity，任何历史变化或身份不匹配都要求重建。

### 13.2 幂等

相同 `turn_id` 再次提交：

- 消息 ID、source digest 和算法身份一致：确认已处理，不重复学习；
- 任一身份不同：抛出冲突错误；
- 不允许用 upsert 覆盖历史事实。

### 13.3 schema 生命周期

首版使用全新 schema v1，不读取旧 Akasha schema。旧库到新库的迁移方式是从
事实源重建。以后 schema 变化必须显式提升版本；不提供静默自动迁移。

## 14. Akasic Agent 插件契约

### 14.1 插件构造

`memory_plugin.py` 实现：

```text
MemoryPlugin.plugin_id = "akasha"
MemoryPlugin.ensure_workspace_storage(...)
MemoryPlugin.build(deps) → MemoryPluginRuntime
```

`MemoryPluginRuntime` 提供：

- `engine=AkashaMemoryEngine`；
- `admin=engine`；
- `embedding_api=engine.embedding_api`；
- 数据库连接和事件订阅对应的 `closeables`。

只有 adapter 层导入 Akasic Agent 的 `core.memory.*`、`TurnCommitted` 和配置类型。
domain、application、SQLite store 均不能反向依赖 host。

### 14.2 `MemoryEngine` 方法

| 接口 | 首版行为 |
|---|---|
| `query` | context/answer/interest/procedure 使用显式模式补全；timeline 明确 unsupported |
| `ingest` | 仅接受带稳定 message IDs 的 `conversation_turn`；主入口由 `TurnCommitted` adapter 调用 |
| `mutate` | remember/forget 返回 `accepted=False, status=unsupported` |
| `reinforce_items_batch` | 明确的确定性 no-op；外部引用不参与无监督可塑性 |
| `describe` | 声明 rich memory、semantic retrieval、context block、graph relation 能力 |
| `tool_profile` | 只公开 recall；不公开 memorize、forget、reinforce |
| `keyword_match_procedures` | 返回空列表；Akasha 不是 workflow engine |
| 时间事件与 dashboard 读取 | 查询 `memory_events`、bindings、engrams 和 trace |
| dashboard update/delete | 抛出明确的 unsupported operation |
| dashboard similar | 返回 dense 相似与显式关联 provenance，但不产生学习 |

`reinforce_items_batch` 的 no-op 是领域选择，不是异常 fallback：该接口没有返回值，
而 Akasha 的学习责任只属于 `MemoryCycle`。实现必须通过注释、descriptor notes
和测试明确这一点。

### 14.3 返回映射

每个完成 turn 映射为一个 `MemoryRecord`：

```text
MemoryRecord
├── id: stable turn/index binding ID
├── kind: "episodic_turn"
├── summary: 从 sessions.db 解析的 user + assistant 摘要
├── score: 读出后的可访问质量
├── engine_kind: "akasha"
├── evidence
│   └── EvidenceRef(kind="message_range", refs=[user_id, assistant_id])
└── signals
    ├── seed evidence
    ├── completion mass
    ├── basin identity
    ├── engram paths
    ├── temporal paths
    ├── effective age/survival
    └── served/learned state version
```

`text_block` 只在 `intent=context` 时生成，并受调用方 context 预算控制。
`MemoryRecord.summary` 不是新的事实副本；它是本次查询从原始消息渲染出的视图。

## 15. 代码边界

```text
akasha-v2-engine/
├── src/akasha/
│   ├── domain/          # feature、graph、diffusion、readout
│   ├── application/     # shared cycle、online runtime、rebuild
│   ├── infrastructure/  # sessions/index、SQLite、writer lease
│   ├── engine.py        # Akasic MemoryEngine adapter
│   └── memory_plugin.py # Akasic MemoryPlugin factory
├── scripts/             # rebuild、parity、report、contract
└── tests/
    ├── unit/
    └── integration/
```

依赖方向：

```text
Akasic Agent adapter
          │
          ▼
application ─────────▶ domain
     │                    ▲
     ▼                    │
application ports ◀── infrastructure
```

domain 不导入 SQLite、Akasic Agent 或网络客户端。`memory_plugin.py` 和
`engine.py` 使用相对导入，使整包以后可以作为 `plugins.akasha` 放入 host，而
不用改写核心模块。

## 16. 错误处理

信任边界集中在：

- Akasic Agent `MemoryQuery` / `MemoryIngestRequest`；
- `TurnCommitted`；
- config 加载；
- sessions/akasha SQLite 读取；
- embedding 响应；
- rebuild CLI。

边界通过后，domain 信任已经验证的 dataclass 和不变量，不在每层重复
`None`、空字符串和默认值检查。

以下情况必须 fail-fast、fail-loud：

- query 缺少 timestamp；
- committed turn 缺稳定消息 ID；
- source digest 不一致；
- 已存在 embedding 的维度错误、非有限值或模型身份变化；
- tokenizer/词典身份变化；
- turn 逆序或同 ID 内容冲突；
- schema/algorithm identity 不兼容；
- SQLite 损坏；
- residual push 违反质量守恒；
- 事务提交失败。

只捕获能够在当前位置转换或恢复的具体异常。不得用空召回、默认向量、跳过
turn、动态 import 或宽泛异常吞掉问题。

## 17. 并发和恢复

- 图写入采用单写者语义；
- query 从带 `state_version` 的不可变快照读取；
- SQLite 事务序列化 `MemoryUpdate`；
- 进程崩溃时未提交事务自动回滚；
- 启动时扫描事实源中晚于最新 `processed_turns` 的 committed turn；
- 缺 ticket 的 turn 通过同一 retrieve/commit 过程重算；
- 重算必须记录原因，不视作正常 ticket 命中；
- 关闭时撤销事件订阅并关闭所有数据库资源。

首版不引入分布式锁或多进程写入。多个进程同时指向同一个可写 sidecar 属于
配置错误，启动时应通过 writer lease 明确失败。

## 18. 验证与验收

### 18.1 单元测试

覆盖：

- 证据融合、sparsemax 和稳定 tie-break；
- 增量 BM25 与批量重算一致；
- burst 只使用过去信息；
- residual push 的 reserve 下界、残差误差和质量守恒；
- engram 因子化传播与概念矩阵等价；
- 时间关系方向性；
- 连接预算阻止重复输入无限增长；
- 资源消耗、恢复和阈值调节；
- 经验复现使偶然节点逐渐弱化；
- 不同 `PYTHONHASHSEED` 的确定性。

### 18.2 合成机制实验

公共测试使用合成内容，不提交用户私人对话：

1. `a/c/e → a/b/c → b/c/d` 验证稳定核心和偶然 `e` 的自净化；
2. burst 内插入无关节点，后续多个独立 burst 重复主故事，验证噪声流入质量下降；
3. 短句依靠累计 burst context 找回前文，信息充分的新主题不被旧 context 吞没；
4. 高频重复任务在连接预算和惊喜度作用下不无限强化；
5. 只出现一次的事件仍能通过一次性 engram 被部分 cue 找回；
6. 相似语义但不同经历的 sparse overlap 低于 dense similarity 所暗示的混淆，
   同一经历的不同 cue 又能完成到同一 basin。

### 18.3 V8 私有效果基线

真实 `sessions.db`、冻结查询和完整召回正文属于本地私有验收材料，放入
`private-data/` 并由 `.gitignore` 排除。

验收比较：

- 每个 query 是否仍覆盖冻结 V8 的目标情景；
- 召回集合差异和长尾掉落；
- basin、completion mass 和 provenance；
- 噪声条目变化；
- 每轮 sparse index、reserve、integrated activation；
- engram、temporal relation、plasticity、context state；
- 最终 logical state hash。

排名不是首要目标，但冻结候选不能因小改动无声消失。任何差异必须生成可读
parity report，不能只报告 aggregate score。

### 18.4 在线—重放等价

用同一合成 turn stream 建立两份独立数据库：

```text
DB A：逐轮走 MemoryEngine.query + TurnCommitted adapter
DB B：走 rebuild CommittedTurnSource
```

逐事件比较 canonical snapshot，最终比较 logical state hash 和 query recall
集合。测试不能让两条路径共享同一个已变更数据库，也不能用重放结果直接喂给
在线路径。

### 18.5 故障测试

必须覆盖：

- embedding miss；
- embedding model/config mismatch；
- tokenizer identity mismatch；
- duplicate turn identity conflict；
- out-of-order turn；
- query 后、commit 前图版本变化；
- SQLite 事务各阶段崩溃注入；
- 临时重建失败时旧目标库保持完整；
- `effect=read_only` 不改变 logical state hash。

### 18.6 性能

在同一冻结快照上记录：

- 全量重建总时间、峰值 RSS 和数据库大小；
- 单轮 indexing、completion、readout、commit 耗时；
- residual push 访问节点/边数量；
- NumPy dense 批量计算与 BM25 倒排候选成本。

远程 embedding 延迟不计入图引擎性能。当前本地数据规模下，完成扩散与读出、
事务提交各自的 p95 必须低于 1 秒，才能标记为可在线使用。性能优化不得改变
canonical recall set 或 logical state hash。

## 19. 论文要求的可证伪边界

### 19.1 Pattern separation

`PatternSeparationTrace` 至少记录：

- dense cosine similarity；
- sparse support overlap；
- weighted Jaccard；
- basin overlap；
- write responsibility；
- cross-story confusion。

证明目标不是“最终只激活少量节点”，而是：

1. 相似但属于不同经历的输入，在 sparse/basin 空间比 dense 空间更少混淆；
2. 同一经历的不同 partial cue 仍能落到共同 basin；
3. 增强分离后，冻结 query 的 storyline coverage 不显著下降。

如果只能满足前两项中的一项，就不能宣称完成模式分离。

### 19.2 Pattern completion

完成必须通过反事实消融验证：

- 完整图；
- 去掉 engram；
- 去掉 temporal relation；
- 只保留 direct seed。

只有 seed 未直接命中、但完整图通过可审计路径恢复的目标 turn 才计为 completion。
返回更多候选本身不构成模式补全证据。

### 19.3 Adaptive forgetting

遗忘实验必须跟踪同一噪声节点在后续独立复现中的：

- raw membership；
- effective membership；
- residual inflow；
- final readout inclusion；
- 与稳定核心的相对质量。

只有它相对核心持续下降并最终退出读出，同时核心仍可完成，才能证明自净化机制
成立。

## 20. 交付边界

实现完成后的仓库交付必须包括：

- 根目录 `AGENTS.md`，采用本项目已确认的中文、fail-loud 和叙事式函数规范；
- 完整源码、CLI、测试和 README；
- `private-data/` 写入 `.gitignore`；
- 首次 commit；
- 通过当前 Akasic Agent 接口契约测试；
- 通过合成机制测试、V8 私有 parity 和在线—重放等价测试；
- 使用当前已认证 GitHub 账号创建公开仓库 `akasha-v2-engine`；
- push 首次提交并确认远端 visibility 为 public。

首版实现、真实重放、协议验证和公开仓库发布状态记录在
`docs/validation.md`。

## 21. 参考资料

1. *Position: Hippocampal Explicit Memory Is the Cornerstone for AGI*,
   arXiv:2606.11245, <https://arxiv.org/abs/2606.11245>
2. *RF-Mem*, arXiv:2605.05097,
   <https://arxiv.org/abs/2605.05097>
3. Andersen, Chung, Lang, *Local Graph Partitioning using PageRank Vectors*,
   FOCS 2006.
4. Oja, *Simplified neuron model as a principal component analyzer*,
   Journal of Mathematical Biology, 1982.
5. Bi and Poo, *Synaptic Modifications in Cultured Hippocampal Neurons*,
   Journal of Neuroscience, 1998.

其中第 1 篇提供显式记忆计算要求和术语；第 2 篇只作为检索组织的旁证，不是
Akasha 架构主线；PageRank、Oja 和 STDP 文献分别支撑局部收敛扩散、归一化竞争
和有向时间可塑性的工程选择。
