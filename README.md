# Akasha V2 Engine

Akasha V2 是面向 Akasic Agent 的无监督显式记忆引擎。它把每个已提交的
`user + assistant` turn 编码为稀疏索引，通过因子化 engram、有向时间关系、
Residual Push 模式补全、局部竞争和经验复现遗忘在线生长。

```text
query dense / BM25 / causal burst / time
                    │
                    ▼
              sparse seed
                    │
                    ▼
       local Residual Push completion
                    │
             ┌──────┴──────┐
             ▼             ▼
       RecallResult    MemoryUpdate
                            │
                 ┌──────────┴──────────┐
                 ▼                     ▼
           engram hyperedge      temporal relation
```

首个公开版本实现了四个硬约束：

- `sessions.db` 是事实源，Akasha SQLite 是可删除并重建的 sidecar；
- 在线逐轮增长与离线重放调用同一个 `MemoryCycle.retrieve/commit`；
- 没有 fast/slow/tag/reinforce 或用户确认分支；
- 相同事实源、embedding、配置与代码产生相同逻辑状态。

缺少 dense 的完整 turn 仍通过 BM25 和时间证据进入记忆；已存在的坏向量、
损坏数据库、逆序历史或身份冲突会直接失败。

## 安装

要求 Python 3.11 或更高版本。

```bash
python -m pip install -e '.[dev]'
```

核心包只依赖 NumPy 和 Jieba。`akasha.engine`、`akasha.memory_plugin` 是
Akasic Agent adapter，需要在 Akasic Agent 源码可导入的环境中加载。

## 从 `sessions.db` 重建

```bash
akasha-rebuild \
  --sessions-db /path/to/sessions.db \
  --db-path /path/to/akasha.db \
  --embedding-model text-embedding-v4 \
  --run-report /path/to/rebuild.json
```

如果目标数据库已经存在，CLI 会先生成带 UTC 时间戳的 `.bak-*` 备份，再原子
替换目标。构建中使用的临时稀疏索引不会写入仓库。

要把指定 query 的完整召回保存为本地审计报告：

```bash
python scripts/export_recall_report.py \
  --memory-db /path/to/akasha.db \
  --sessions-db /path/to/sessions.db \
  --output /path/to/recall-report.md
```

报告包含原始 user query、每条召回的 user 内容与截断到 50 字的 assistant
回复。真实对话、数据库和报告必须放在 `private-data/` 或仓库外。

## 在线运行

`MemoryPlugin.plugin_id` 保持为 `akasha`，并实现 Akasic Agent 当前的
`MemoryPlugin`/`MemoryEngine` protocol。宿主在 query 阶段得到非变异的
`RetrievalTicket`；持久化 `TurnCommitted` 到达后，adapter 从真实消息 ID
读取 turn、补齐 embedding，并调用同一个 cycle 完成学习和原子落库。

```text
MemoryEngine.query
  └─ OnlineMemoryRuntime.query_turn
       └─ MemoryCycle.retrieve

TurnCommitted
  └─ OnlineMemoryRuntime.commit_from_source
       └─ MemoryCycle.commit
            └─ atomic SQLite snapshot
```

同一 sidecar 具有单写者 lease。第二个进程指向同一路径时启动失败，避免两个
图状态相互覆盖。进程崩溃后，下一次启动会从 `sessions.db` 增量补齐 sidecar
尚未提交的 turns。

adapter 契约可以对当前 Akasic Agent checkout 做结构检查：

```bash
python scripts/check_akasic_contract.py \
  --host /path/to/akasic-agent
```

## 机制

每个 turn 的 seed 是 dense、BM25、时间、累计 burst context 与 surprise 的
非线性融合。信息充分的新 query 会抑制旧 context；短句和省略句会更多借助
当前 burst。seed 进入带重启的局部 Residual Push：

\[
x^* = \alpha s + (1-\alpha)P^\top x^*
\]

算法返回的 `reserve` 是固定点逐元素下界，`residual_l1` 是未结算质量的误差
界。共同激活被写成 \(O(k)\) 的 engram memberships，而不是 \(O(k^2)\) clique；
同 burst 的先后关系写成正向强、反向弱的时间边。

Oja 风格竞争、连接预算、突触资源消耗/恢复、可塑性阈值和经验复现遗忘共同
限制自我强化。召回能够影响下一轮学习，但纯递归激活得到的 credit 弱于新的
外部证据。

## 验证

本仓库包含：

- Residual Push 下界和质量误差测试；
- 稀疏索引增量一致性与缺 dense 测试；
- 连接预算、抑制、资源恢复和经验复现遗忘测试；
- 不同 `PYTHONHASHSEED` 的确定性测试；
- 在线逐轮写入与干净重放的逻辑状态等价测试；
- 单写者冲突测试；
- Akasic Agent protocol 检查。

私有验收在 4,858 个真实 turns 上重放，11 个冻结 query 的召回集合与稳定 V8
逐条完全一致。公开仓库不包含这些对话。可复核的统计与边界见
[验证报告](docs/validation.md)。

## 文档

- [系统设计规格](docs/spark/2026-07-27-akasha-v2-memory-engine-design.md)
- [实现与验证报告](docs/validation.md)

## License

[MIT](LICENSE)
