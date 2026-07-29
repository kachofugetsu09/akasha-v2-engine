# Akasha V2 实现与验证报告

## 结论

首版已经形成可在线运行、可从事实源重建、可审计的显式记忆引擎。真实私有
快照上的 11 个冻结 query 与稳定 V8 的召回集合逐条一致；合成在线事件流与
离线重放得到相同逻辑状态。

这里的“一致”指候选 turn 集合完全相同，不只是数量相同或总体分数接近。

## 验收数据

私有数据不进入 Git。验收使用只读 SQLite snapshot：

| 项目 | 结果 |
|---|---:|
| source messages | 12,399 |
| committed turns | 4,858 |
| sessions | 25 |
| 缺少 dense、仍被索引的 turns | 68 |
| engram hubs | 4,684 |
| graph relations | 21,520 |
| 冻结 query | 11 |
| 全量重放时间 | 115.71 秒 |
| 峰值 RSS | 508,080 KiB |
| logical state SHA-256 | `3970f2d077a32de434164c19f969512ec40c504c668be679521668675ce0256c` |

68 个缺 dense 的 turns 主要来自重复 scheduler 内容。它们没有被跳过或伪造
向量，而是依靠 lexical/time 特征参与因果学习。

## V8 召回集合 parity

| query user_seq | V8 | V2 | 集合差异 |
|---:|---:|---:|---:|
| 3011 | 10 | 10 | 0 |
| 4740 | 19 | 19 | 0 |
| 5294 | 8 | 8 | 0 |
| 7877 | 16 | 16 | 0 |
| 8464 | 13 | 13 | 0 |
| 8566 | 20 | 20 | 0 |
| 9224 | 5 | 5 | 0 |
| 9624 | 15 | 15 | 0 |
| 9710 | 16 | 16 | 0 |
| 9892 | 28 | 28 | 0 |
| 10306 | 11 | 11 | 0 |

本地复核命令：

```bash
python scripts/check_v8_parity.py \
  --baseline private-data/v8-baseline.json \
  --memory-db private-data/akasha-v2.db
```

脚本会列出每个 query 的 `missing` 与 `unexpected`，任一集合漂移就以非零退出。

## 在线与重放等价

集成测试构造真实 `sessions.db` schema，并分别执行：

```text
路径 A：启动 OnlineMemoryRuntime
        ├─ 恢复历史 prefix
        ├─ query_turn
        │    ├─ 临时 advance 小型 RetrievalState
        │    ├─ 读取同一个 committed graph
        │    └─ finally 还原时钟与容量
        └─ TurnCommitted
             ├─ stage_from_source
             └─ publish_staged
                  ├─ 校验 state_version
                  ├─ 原地应用 RetrievalState 与学习
                  ├─ 原子写 SQLite snapshot
                  └─ 失败时从 durable prefix 恢复

路径 B：同一 source index
        └─ rebuild_memory
```

最后比较 turn、graph edges、hub、plasticity、seed evidence、context 和 burst
members，而不是只比候选条数。测试同时覆盖：

- 持久化 prefix 恢复后继续增长；
- staged suffix 发布前不改变 graph snapshot，重启后能确定性补齐；
- query 不复制 graph 且返回后完整还原 published graph；
- 首次写入和已有 prefix 写入失败后都从持久化真源恢复；
- stale ticket 在最新 state 上重算；
- rebuild 预分配图与在线动态扩容一致；
- source 中缺 dense 的 turn 不被丢弃；
- 同一路径的第二个 writer 被拒绝。

## 确定性

确定性测试用不同 `PYTHONHASHSEED` 启动独立 Python 进程重建同一 fixture，并
比较 canonical logical state。生产实现还固定：

- 全局 turn 因果排序；
- UTF-8 tie-break；
- sparse support 排序；
- residual heap 的稳定二级键；
- NumPy dtype 和浮点聚合顺序；
- tokenizer、词典、embedding model 和算法配置身份。

SQLite 文件字节会包含代码/环境审计 metadata，因此工程判等使用 canonical
logical state hash。

## 接口验收

adapter 已按真实 Akasic Agent checkout 验证：

```json
{
  "engine_contract": true,
  "plugin_contract": true,
  "plugin_id": "akasha"
}
```

`MemoryPlugin` 暴露 engine、admin、embedding API 和 closeables。查询阶段不
改变图；`TurnCommitted` 是唯一事实写入入口。手工 admin remember、forget
和外部 reinforce 不参与无监督学习。随已提交 turn 持久化的 Message feedback
是例外：remember 只增强目标 turn 在自身 episode 中的 membership，forget
保留目标作为联想传播桥，但从最终召回结果和后续独立可塑性支持中隐藏。
一次 remember 在 log-weight 空间应用现有图学习率：
`gain = boost ** learning_rate`，随后重新归一化 episode 与 source 的连接预算；
它不会增加局部总传导预算，也不会给整个语义簇或 query seed 追加奖励。

## Message feedback 冻结快照实验

2026-07-30 使用线上 `sessions.db` 的只读 SQLite backup 做了 4,840 个 turn
的 baseline、remember 与 forget 全量因果重放；原库、运行中索引和服务均未修改。
移除全部 feedback marker 后重建，16 张非 metadata 表与线上状态逐行一致，
因此以下差异只来自 Message feedback。

17 次真实 remember 的图节点和边集合完全不变。21,443 条关系中，raw weight
变化超过 `1e-6` 的有 56 条；membership raw L1 变化占原总质量
`0.0773%`。全部 4,840 个 query 的 Top-1 保持率为 `99.55%`，平均 Jaccard
为 `0.9951`；与任一目标 embedding 相似度低于 `0.30` 的 117 个 query
Top-1 全部不变。被标记内容进入召回的 query 比例从 `2.07%` 升至 `2.52%`，
睡眠语义簇从 `17.51%` 升至 `19.87%`。11 个重点 query 的完整候选顺序均未
变化，包括两条“昨晚睡得怎么样”审计 query；因此该机制证明了局部增强，
但不声明它单独修复了历史 snapshot 误用事故。

forget 实验从真实纠错中选择 7 个 marker、8 个错误 turn。marker 前 3,613
个 query 完全一致；marker 后 1,227 个 query 的 Top-1 保持率为 `99.02%`，
平均 Jaccard 为 `0.9874`。8 个目标的最终召回率从 `3.10%` 降为 `0`；
与目标相似度低于 `0.30` 的 84 个 query 的完整候选顺序全部不变。被忘内容
仍参与 residual diffusion，差异来自最终读出过滤、独立可塑性支持移除和
burst context 移除，而不是删点或断边。相关语义簇会继续因后续因果学习产生
可见变化，这是保留联想传播的预期 tradeoff。

## 测试边界

公开测试验证数学不变量、领域闭环和接口结构。真实对话的召回正文、embedding
与数据库都只留在 `private-data/`。因此公开 CI 能证明机制没有回归，但 V8
私有 parity 仍需持有相同 snapshot 与冻结 capture 才能重跑。

当前不作以下声明：

- 系统完整复现了生物海马体；
- 仅凭现有案例已经普遍证明 pattern separation；
- 显式记忆本身足以构成 AGI；
- 现有参数适合所有用户分布。
