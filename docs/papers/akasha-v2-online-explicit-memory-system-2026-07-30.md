# Akasha V2：面向长期对话代理的在线稀疏情景记忆

## 基于动态事件边界、经验复现生存、因子化 Hebbian 图与 Message 反馈的因果记忆系统

**文档类型：** 系统论文式技术报告  
**版本日期：** 2026-07-30
**代码基线：** `single_state_empirical_recurrence_survival_v9_feedback`
**代码仓库：** [`akasha-v2-engine`](https://github.com/kachofugetsu09/akasha-v2-engine)
**实验数据：** 冻结 `sessions.db` 派生的 4,858 个完整 user–assistant turn（包含 17 次 remember + 7 次 forget 反馈标记）
**研究状态：** 已完成首版交付；具备在线增量事务、崩溃恢复、在线—重放等价与确定性验证
**稿件层：** 正文稿

## 摘要

长期对话代理需要从持续增长的交互历史中恢复个人经历、事件前因和跨轮关联。单次 dense 或 BM25 检索擅长寻找表面相似内容，却难以从一句局部线索重建完整情景；把全部历史放进上下文又会带来成本、重复和干扰。本文提出 Akasha V2，一种无外部确认信号的在线稀疏情景记忆系统。系统把一轮 user–assistant 对话作为原子 turn，以当前查询、BM25、dense、时间间隔和 stream-local burst 构造因果稀疏 seed；共同激活的 turn 通过情景 hub 因子化存储，并写入不对称时间边。读取阶段采用双路读出架构：一路利用活跃 burst 上下文做全图扩散，另一路仅凭当前 query 独立寻址，两路通过共享稀疏竞争合并。学习阶段采用 Oja-like 竞争、双侧连接预算、突触资源和活动阈值，使共同激活关系可增强，使暴露但缺少共同活动的关系可减弱。

Akasha V2 从真实对话中直接复现间隔因果学习双 lognormal 生存函数，把关系的原始结构强度与当前有效可达性分离。Agent 发起的 remember 与 forget 标记作为因果事实源的一部分，在 episode 内部以 log-weight 空间增强或从独立可塑性支持与最终召回中抑制目标 turn——不删除图拓扑、不改变总传导预算。

我们对单用户真实对话快照进行严格 read-before-write 全量重放。最终图包含 4,684 个情景 hub 和 21,520 条关系。11 个预先关注的冻结 query 与稳定 V8 的召回集合逐条一致（零差异）。在线事件流与离线重放得到相同逻辑状态。17 次真实 remember 在保持图拓扑不变的同时将目标内容召回率从 2.07% 升至 2.52%，7 次 forget 将错误目标最终召回率从 3.10% 降为 0。结果表明 Akasha V2 已形成可审计的跨 burst 联想、模式补全、行为层自净化和人类反馈对齐能力；已可作为独立显式记忆引擎部署。

**关键词：** 显式记忆；情景记忆；模式补全；经验遗忘；再巩固；Hebbian learning；事件分段；Personalized PageRank；Residual Push；长期对话代理

## 1. 引言

参数化语言模型在预训练中吸收大规模统计规律，可以把 embedding 视作一种已学习的隐式表征。一次个人经历却不会自动进入模型参数，模型也很难仅凭局部线索恢复这段经历的时间、来源和相邻事件。长期对话代理因此需要一套独立的显式记忆：它要保存一次经历，也要在新的局部线索到来后恢复整段情景。

本文关注显式记忆如何从连续对话中自行形成。系统把完整 user–assistant turn 写成原子节点，利用 dense、BM25、时间和当前 burst 产生稀疏活动；共同活动形成情景 hub，先后出现的内容形成有向时间关系。下一轮输入先读取旧图，再把本轮活动写回图。检索会改变后续关联，后续关联又会改变将来的检索。

Park 将人工显式记忆概括为八项计算要求：稀疏索引、非误差驱动更新、关联构造、模式分离、模式补全、动态性、单次快速可塑性和适应性遗忘 [1]。该框架给出了目标，却没有规定可直接部署的索引、图结构与收敛算法。Akasha 把这些计算要求落实为一套可重放、可消融、可检查来源的因果状态机。

本文研究一个更窄、可证伪的问题：

> 在没有用户确认、奖励标签和 LLM 事后打分的条件下，长期对话系统能否仅依赖历史内容、时间结构和自身召回活动，在线长出可用于情景补全的稀疏关联图？

围绕这个问题，本文作出六项工程贡献：

1. 定义 turn 级因果编码，将当前查询、dense、BM25、时间和活动 burst 转换为数据依赖的稀疏 seed；
2. 用情景 hub 因子化存储共同激活模式，并以不对称时间边保留故事顺序，避免每次共同激活写入 \(O(k^2)\) clique；
3. 用带重启的局部 Residual Push 计算查询钳制的唯一稳态，并以 residual 质量提供停止依据；
4. 采用双路读出架构：contextual route 利用活跃 burst 上下文、independent route 仅凭 query 独立寻址，两路通过共享稀疏竞争合并；
5. 从直接 dense/BM25 复现间隔学习双时间尺度生存函数，以部分支持恢复统一表达遗忘和再激活；
6. 引入 Agent 发起的 Message 级反馈（remember/forget）作为因果事实源的一部分，在 episode 内定向增强或抑制，不改变图拓扑结构。

本文的主张限于一个研究原型。文中“脑科学启发”表示工程机制与若干已知计算原则相似，不表示软件节点、边或数值状态与生物神经元、突触一一对应。

## 2. 相关工作与设计依据

### 2.1 显式记忆的计算要求

Park 将 dense 表示 \(E\) 映射为稀疏索引 \(S\)，再通过关联矩阵 \(A\) 完成部分模式：

$$
S=\operatorname{sparsify}(E),
$$

$$
S^{\mathrm{retrieved}}
=
\sigma\left(A S^{\mathrm{partial}}\right).
$$

该论文强调，稀疏并不自动等于模式分离；不同输入的稀疏代码仍需避免比 dense 表示更加混淆 [1]。Akasha 沿用了“dense 表示提供线索、稀疏索引负责显式绑定、关联结构负责补全”的分工。本文没有声称已经证明严格的相似度非扩张条件。

### 2.2 事件边界与时间上下文

连续经历会被组织成离散事件。Pu 等人的行为实验和计算模型表明，事件边界会重置时间上下文，并同时改善事件内时间组织、削弱跨事件时间顺序 [4]。Zheng 等人在人类内侧颞叶记录到对抽象认知边界响应的神经元；边界后的神经状态变化与后续识别和顺序记忆相关 [5]。这些结果支持在对话中维护活动 burst，也提示当前 burst 与历史情景需要不同的读取职责。

Akasha 不用固定“最近 \(N\) 条”定义 burst。真实数据的时间间隔呈现明显短、长两种尺度，但分界会随样本变化；因此时间只作为因果先验，还需结合 dense、BM25 和当前句的信息充分程度。

### 2.3 模式分离、关联与竞争

人类 fMRI 研究为 DG/CA3 中相似输入的模式分离提供了证据 [6]。时间不对称的突触可塑性模型说明，紧密时序和相关输入可以形成有向关联；竞争机制会让有效输入增强、低效输入减弱 [7]。Oja 规则进一步说明，Hebbian 增强可以通过权重相关的负项保持有界 [8]。

本文据此使用三种工程机制：

- 稀疏投影减少一次事件的活动支持集；
- 过去到当前的时间边强于当前到过去的边；
- Oja-like 负项和连接预算限制持续正反馈。

这些对应是计算类比。对话 turn 的分钟级间隔不等同于生物 STDP 的毫秒级时间窗。

### 2.4 突触资源与可塑性阈值

元可塑性指先前活动改变后续产生 LTP/LTD 的难易程度 [9]。短时突触抑制也被用于解释高频刺激下的增益控制和网络稳定 [16]。Akasha 为每条关系保存一个可恢复资源和一个活动后升高、随时间回落的阈值。它们只调节“这次刺激能写多少”，不直接改变读取权重。

### 2.5 图扩散与稀疏选择

Personalized PageRank 将外部 seed 持续注入随机游走，给定 seed 后存在唯一固定点。Forward Push 可以局部计算 PPR，并可从线性不变量解释为 Gauss–Seidel 类更新 [12]；后续工作给出了绝对误差保证的单源 PPR 近似 [13]。Akasha 使用相同的“reserve + residual”思想，将停止条件写成剩余质量，而非固定 hop。

Sparsemax 可以把 dense 分数投影成有限支持的概率分布 [11]；\(\alpha\)-entmax 将该思想推广为一族数据依赖的稀疏映射 [14]。Akasha 在 seed 和 basin 选择中使用 sparsemax，在事件成员和长尾选择中使用 1.5-entmax 或更高 \(\alpha\) 的 entmax。

### 2.6 相邻系统的边界

RF-Mem 根据检索分数的均值和熵，在 Familiarity 与 Recollection 两种读取方式之间切换；Recollection 会聚类候选，并用聚类中心改写后续查询 [2]。它提供了一个有用观察：线索越含糊，读取范围越需要扩大。Akasha 将这一观察用于调节上下文依赖和 basin 温度。两套系统保存的状态不同：RF-Mem 在读取时临时生成新查询，Akasha 持久保存历史共同活动和时间关系。

Memini 为记忆边设置 Benna–Fusi 式多时间尺度状态，用状态耦合表达巩固与遗忘 [3, 10]。Akasha V2 不保存一组耦合的隐藏快慢权重，而是显式区分原始学习强度与当前有效可达性：前者保存 engram 结构，后者由本库直接复现间隔学习出的生存函数调节。资源和阈值负责限制本次可塑性，经验生存函数负责长期读取衰减。这个单状态方案更容易审计，但不具有 Benna–Fusi 级联状态的长期容量理论保证。

### 2.7 检索后更新、竞争性遗忘与数据驱动时间尺度

记忆检索不是无副作用的读取。再激活后的记忆会进入可更新状态 [15, 18]；中等强度激活可能增加随后遗忘 [19]；目标检索也会通过竞争项目的表征抑制产生适应性遗忘 [20]。这些研究支持“召回应改变后续可访问性”这一结构假设，却不能直接提供 AI 对话中应使用的天数常数。

Akasha V2 因此只迁移结构、不迁移时间参数：复现间隔模型只观察当前 query 的直接 dense/BM25 seed 何时再次命中历史 turn，以因果在线统计学习短、长两个 lognormal 分量。图扩散产生的 recurrent activation 不参与复现间隔估计，避免系统用自己的联想输出塑造遗忘先验。关系被共同激活后只按连续信用部分恢复，不会一次召回便重置为“刚发生”。

## 3. 问题定义

### 3.1 因果 turn 流

令长期对话历史为按提交时间排序的 turn 流：

$$
\mathcal D_t=\{T_0,T_1,\ldots,T_{t-1}\}.
$$

每个 turn 是已经提交的一轮完整交互：

$$
T_i=
\left(
u_i,a_i,e_i^u,e_i^a,l_i^u,l_i^a,\Delta t_i,\operatorname{stream}_i
\right),
$$

其中 \(u_i,a_i\) 是 user 与 assistant 文本，\(e\) 是 dense 向量，\(l\) 是词项频率，\(\Delta t_i\) 是同一 stream 的输入间隔。

系统处理当前 user 输入 \(u_t\) 时只能用 \(\mathcal D_t\) 完成读取和 burst 判断。assistant 完成后，\(T_t\) 才能进入图。该约束防止当前答案和未来消息泄漏进本轮召回。

### 3.2 工作记忆与显式输出

设 \(B_t\subset\mathcal D_t\) 是当前 stream 的活动 burst，\(V_t\) 是上层 LLM 已经可见的 turn。系统允许 \(B_t\) 参与 seed、扩散和写边，但最终显式输出为：

$$
R_t=
\operatorname{Completion}(u_t,B_t,G_t)\setminus V_t.
$$

这样，当前 burst 充当工作记忆，图检索负责补回窗口之外的旧情景。

### 3.3 优化目标

该系统同时追求：

1. **旧情景存在性**：相关旧 burst 至少恢复一个可供 LLM 展开的锚点；
2. **故事覆盖**：允许恢复较远前因和后果，不局限于最相似 turn；
3. **受控噪音**：允许少量长尾，不让单一大簇占满 prompt；
4. **局部计算**：代价主要随 seed 周围访问区域增长；
5. **因果可复现**：相同输入、配置和代码产生相同语义图与召回。

本研究优先评估“有或无”和 storyline 覆盖，排序只用于防止候选在小改动后掉出支持集。

## 4. 系统概览

```text
┌─────────────────────────────────────────────────────────────┐
│ 当前 user 输入 q_t                                          │
└──────────────────────────────┬──────────────────────────────┘
                               │
             ┌─────────────────▼──────────────────┐
             │ 因果特征                           │
             │ dense / BM25 / 时间 / 活动 burst   │
             └─────────────────┬──────────────────┘
                               │ 稀疏 seed s_t
             ┌─────────────────▼──────────────────┐
             │ raw engram 匹配 + 可达性竞争       │
             │ 经验复现生存决定 effective weight  │
             └─────────────────┬──────────────────┘
                               │
             ┌─────────────────▼──────────────────┐
             │ 局部 Residual Push                 │
             │ reserve 下界 + residual 剩余误差   │
             └──────────────┬───────────────┬─────┘
                            │               │
                  sharp completion     活动情景 basin
                            │               │
                            └───────┬───────┘
                                    │
                     ┌──────────────▼──────────────┐
                     │ 去重与可见性约束            │
                     │ 输出旧 burst 的完整 turn    │
                     └──────────────┬──────────────┘
                                    │
                         LLM 生成 assistant 回复
                                    │
                     ┌──────────────▼──────────────┐
                     │ read-before-write 学习      │
                     │ hub + 时间边 + 部分支持恢复 │
                     └──────────────┬──────────────┘
                                    │
                           持久图状态 G_{t+1}
```

图中有两类节点：

- **turn 节点**：指向完整 user–assistant 内容及其 dense、词项与时间元数据；
- **hub 节点**：表示一次共同激活事件，连接该事件的稀疏成员。

有三类关系：

- turn \(\leftrightarrow\) hub 的双向 membership；
- 过去 turn \(\rightarrow\) 当前 turn 的正向时间边；
- 当前 turn \(\rightarrow\) 过去 turn 的较弱反向回忆边。

## 5. 方法

### 5.1 因果 dense 与 BM25

当前 user dense 对每个历史 turn 的 user、assistant dense 分别计算余弦相似度，并取较大值：

$$
d_i(q_t)=
\max\left(
\langle e_t^u,e_i^u\rangle,
\langle e_t^u,e_i^a\rangle
\right).
$$

BM25 使用截至 \(t-1\) 的文档频率、平均长度和倒排表：

$$
b_i(q_t)=
\sum_{w\in q_t\cap T_i}
\operatorname{IDF}_t(w)
\frac{2.2\,\operatorname{tf}_{i,w}}
{\operatorname{tf}_{i,w}
+1.2\left(0.25+0.75|T_i|/\overline{|T|}_t\right)}.
$$

所有统计量只使用历史前缀。分数随后转成经验尾部惊异度：

$$
\psi(x_i)
=
-\log
\frac{|\{j<t:x_j\ge x_i\}|}{t}.
$$

这一步把不同量纲的 dense 与 BM25 变成各自历史分布中的相对证据，避免直接用固定线性权重相加。

### 5.2 活动 burst 与上下文依赖

系统为每个 stream 独立维护当前 burst 成员。系统接收新查询后先将整个 burst 聚合为 dense 原型和归一化词项，再估计：

- 时间先验 \(p_t^{\mathrm{time}}\)：当前 gap 在历史 gap 中的经验尾部位置；
- 内容连续性：查询与 burst 原型的 dense、BM25 证据；
- 上下文依赖 \(c_t\)：当前 user 词项有效支持数在历史中的相对位置。

词项有效支持数为：

$$
n_{\mathrm{eff}}(q)
=
\exp\left(
-\sum_w p(w\mid q)\log p(w\mid q)
\right).
$$

短句、指代句和低信息输入通常有较小的 \(n_{\mathrm{eff}}\)，因此在历史分布中排名较低，得到高上下文依赖 \(c_t\)。\(c_t\) 是因果 rank：对历史中所有已见 turn 的有效支持数维护有序列表，当前值按其在列表中的尾部位置计算百分位 rank。新 burst 的第一个 turn 无法计算 \(c_t\)，默认为 0.5。连续性信念沿用以下公式，以 time_prior * context_dependence 作为 rescue term：

$$
p_t^{\mathrm{cont}}
=
p_t^{\mathrm{base}}
+\left(1-p_t^{\mathrm{base}}\right)
p_t^{\mathrm{time}}c_t.
$$

系统以 \(p_t^{\mathrm{cont}}\ge 0.5\) 作为 MAP 连续事件。这个 0.5 表示二元后验的 MAP 分界，不是候选数量阈值。当系统判断 burst 延续时，context mass 由 _combine_odds(continuation, context_dependence) 产生，用于按质量在 query seed 与 context seed 之间插值。context_dependence 还决定双路读出中 independent route 的激活比例（§5.13）。

### 5.3 稀疏 seed

当前查询证据与 burst 证据分别投影：

$$
s^{q}
=
\operatorname{sparsemax}
\left(
\psi(d^q)+\psi(b^q)
\right),
$$

$$
s^{B}
=
\operatorname{sparsemax}
\left(
\psi(d^B)+\psi(b^B)
\right).
$$

系统确认 burst 延续后，将连续性与上下文依赖以 odds 相乘得到上下文质量 \(\rho_t\)，再混合：

$$
s_t=(1-\rho_t)s^q+\rho_t s^B.
$$

新 burst 的 \(\rho_t=0\)。稀疏支持集由分数分布决定，没有固定 top-\(K\)。

### 5.4 查询惊喜与一次写入

查询惊喜衡量当前输入无法由历史最佳 dense 与 BM25 证据重建的程度。设两个通道的归一化预测残差为 \(r_d,r_b\)，则：

$$
\operatorname{surprise}_t
=
\sqrt{\frac{r_d^2+r_b^2}{2}}.
$$

该值作为新 hub 和时间边的写入增益。重复、可完全预测的定时任务产生较小写入；带来新内容的输入产生较大写入。它不是 LLM 答案误差，也没有使用用户确认标签。

### 5.5 因子化情景 hub

若一次稳定活动为稀疏向量 \(b_t\)，普通 Hebbian 更新可以写成：

$$
\Delta W_t=\eta b_tb_t^\top.
$$

直接展开会产生 \(O(k^2)\) 边。Akasha 保存一个 hub \(H_t\) 及其 \(k\) 个 membership：

$$
W_{\mathrm{episode}}
=
\sum_t \eta_t b_tb_t^\top
=
B\Lambda B^\top.
$$

传播时执行 turn \(\rightarrow\) hub \(\rightarrow\) turn，存储成本降为每次共同激活 \(O(k)\)。hub 不是预先定义的话题标签，它是读取稳定后共同活动的因子化记录。

### 5.6 有向时间关系

对共同活动中的历史 turn \(i\) 与当前 turn \(t\)，写入：

$$
w_{i\rightarrow t}
\propto
p_t^{\mathrm{cont}}\,
\operatorname{surprise}_t\,
b_t(i),
$$

$$
w_{t\rightarrow i}
=
\gamma w_{i\rightarrow t},
\qquad \gamma=0.25.
$$

正向边表达“过去状态预测当前状态”，反向边允许当前线索回到前因，同时降低逆序扩散的强度。0.25 是当前工程配置，不是由神经实验直接估计的常数。

### 5.7 局部转移与稳态

对节点 \(u\)，令正边权总和为：

$$
d_u=\sum_v w_{uv}.
$$

当前实现先把总连接强度转成可传播 conductance：

$$
c_u=1-e^{-d_u}.
$$

链接转移为：

$$
Q_{uv}
=
c_u\frac{w_{uv}}{d_u}.
$$

剩余的 \(1-c_u\) 返回当前 seed。对固定查询，得到行随机核：

$$
\widetilde P_{uv}
=
Q_{uv}+(1-c_u)s_t(v).
$$

最终稳态满足：

$$
x^\star
=
\alpha s_t
+(1-\alpha)\widetilde P^\top x^\star,
\qquad \alpha=0.25.
$$

由于 \(\widetilde P\) 行随机且 \(0<\alpha\le1\)，映射在 \(L_1\) 范数下的压缩系数为 \(1-\alpha\)，因此给定 seed 后固定点唯一。

### 5.8 Residual Push

系统维护：

- \(p\)：已经结算的 reserve；
- \(r\)：尚未传播的 residual。

初始化 \(p=0,r=s_t\)。每次选择 residual 最大的节点 \(u\)，执行：

$$
p_u\leftarrow p_u+\alpha r_u,
$$

$$
r_v\leftarrow
r_v+(1-\alpha)r_u\widetilde P_{uv},
$$

再清空原 \(r_u\)。reserve 单调增加，是 \(x^\star\) 的逐坐标下界。剩余 residual 经过完整结算后的总 \(L_1\) 质量不超过 \(\|r\|_1\)，因此停止条件为：

$$
\|r\|_1\le\varepsilon,
\qquad \varepsilon=10^{-7}.
$$

固定路径长度和固定 hop 不参与停止判断。实现使用 indexed max-heap，并在相同 residual 时按节点 ID 决定顺序。

### 5.9 共同激活、竞争与可塑性

活动场由三部分构成：

1. 当前 turn，活动为 1；
2. 直接 seed；
3. 归一化后的 reserve。

turn 活动先经过幂次增强，再用 1.5-entmax 投影为事件成员：

$$
b_t
=
\operatorname{entmax}_{1.5}
\left(
\log(1+|\mathcal A_t|)
\cdot a_t^{\,2}
\right).
$$

已暴露 hub membership 使用 Oja-like 更新：

$$
\Delta w_{ih}
=
\eta\,e_{ih}\,a_h
\left(
a_i-a_hw_{ih}
\right),
$$

其中 \(e_{ih}\) 是资源和阈值决定的可塑性 eligibility。成员未共同活动时，括号内可为负，关系被削弱。

每个 hub 的 membership 总质量受预算约束：

$$
\sum_i w_{ih}\le B_h,
\qquad B_h=1.
$$

同一个 turn 分配给不同 hub 的 membership 也共享来源侧预算：

$$
\sum_h w_{ih}\le B_i,
\qquad B_i=1.
$$

每个来源节点的同类时间出边另有独立预算。hub 侧预算避免单个情景无限增长，turn 侧预算避免一个高频节点同时向大量情景提供满强度连接；增强一组关系会压缩同一局部预算中的其他关系，形成异突触竞争。

### 5.10 资源恢复与活动阈值

边 \(e\) 保存资源 \(R_e\in[0,1]\) 与阈值 \(\theta_e\in[0,1]\)。距离上次刺激经过 \(\Delta t\) 后：

$$
R_e
\leftarrow
1-(1-R_e)e^{-\Delta t/\tau_R},
$$

$$
\theta_e
\leftarrow
\theta_e e^{-\Delta t/\tau_\theta}.
$$

给定活动 \(a\)，可塑性资格为：

$$
\operatorname{eligibility}(e,a)
=
R_e
\left[
1-\exp\left(
-a\max(a-\theta_e,0)
\right)
\right].
$$

刺激后，资源消耗、阈值升高。反复密集刺激不会线性累加同等写入；休息后可塑性逐渐恢复。时间常数由历史正 gap 的两个在线几何尺度估计。

### 5.11 经验复现生存与部分支持恢复

Akasha 不为“遗忘几天”指定固定常数。对同一历史 turn，只有当前 query 的 `query_dense` 或 `query_bm25` 直接 seed 再次命中它时，才记录一个复现间隔。令 \(g=\log\Delta t\)，系统按 log-space 距离在线维护短、长两个分量的加权均值与二阶矩。当前生存函数为：

$$
S_\theta(\Delta t)
=
\pi_s\left[
1-\Phi\left(
\frac{\log\Delta t-\mu_s}{\sigma_s}
\right)
\right]
+
\pi_l\left[
1-\Phi\left(
\frac{\log\Delta t-\mu_l}{\sigma_l}
\right)
\right].
$$

关系 \(e\) 保存原始学习强度 \(w_e\) 和上次支持时间 \(t_e^{support}\)。读取阶段使用：

$$
\widetilde w_e(t)
=
w_e S_\theta(t-t_e^{support}).
$$

\(w_e\) 表示学到的结构身份，\(\widetilde w_e\) 表示当前可传播电导。一次共同激活产生连续支持信用 \(c_e\)，恢复比例为：

$$
\rho_e=1-e^{-c_e},
$$

$$
t_e^{support}
\leftarrow
t_e^{support}
+
\rho_e
\left(t-t_e^{support}\right).
$$

因此弱召回只小幅恢复，重复强召回才逐步保持可达性；关系不会因一次低分联想被完全刷新。系统同时累计 direct seed 提供的独立信用和图传播产生的 recurrent 信用用于审计。当前核心恢复公式仍使用总共同激活信用，独立信用尚未成为硬门控。

### 5.12 结构身份与有效可达性分离

若在 basin 匹配、head 选择和图扩散三个阶段都使用 \(\widetilde w\)，同一旧记忆会重复承担遗忘成本。V2 采用三阶段分工：

1. 用原始 membership \(w\) 判断一个历史 hub 是否与当前 cue 匹配；
2. 只在 cue 已选 heads 内比较相对可达性；
3. Residual Push 只沿 \(\widetilde w\) 传播。

对 cue 已选 head \(h\)，定义：

$$
a_h
=
\frac{\sum_{e\in h}\widetilde w_e}
{\sum_{e\in h}w_e}.
$$

\(\{a_h\}\) 经 gain-normalized sparsemax 投影形成可达性支持集。该过程没有固定 top-\(K\) 或绝对年龄阈值；旧情景是否保留由同一 query 下候选 heads 的相对状态决定。通过筛选的 heads 再按原 cue 质量归一化，因而遗忘决定“还能不能有效进入”，不会篡改“它原来属于哪个情景”。

### 5.13 情景 basin 与最终读出

sharp seed 扩散提供局部直接补全。系统还将每个历史 hub 视为一个候选 basin。basin 对当前证据的分数使用原始 membership 归一化后的加权 log-sum-exp：

$$
\operatorname{score}(H)
=
\log\sum_{i\in H}
\bar w_{iH}e^{z_i}.
$$

basin 分数经带温度 sparsemax 选择，温度来自查询惊喜；当系统判断当前是新 burst 时，还乘以事件边界二元熵，使边界不确定时保留更多情景，边界明确时选择更尖锐。随后执行 5.12 节的相对可达性竞争，最终扩散才使用有效权重。

每个活动 basin 独立扩散。最终返回集合是：

$$
R_t
=
R_t^{\mathrm{sharp}}
\cup R_t^{\mathrm{basin\ direct}}
\cup R_t^{\mathrm{basin\ completion}}
\cup R_t^{\mathrm{relative\ tail}}
\setminus V_t.
$$

relative tail 只保留 basin 相对 sharp cue 新增的信息，并要求时间路径可达与 dense–BM25 共同支持。所有返回项保存来源标签和局部路径证据。

## 6. 在线读写循环

完整一轮执行顺序为：

```text
1. 读取 G_t 与 stream-local BurstState_t
2. 用 q_t 和历史前缀推断 burst、seed
3. 在 G_t 上扩散并生成显式记忆 R_t
4. 把 visible burst + R_t 交给 LLM
5. 得到 assistant 回复 a_t
6. 组成完整 turn T_t
7. 用直接复现证据更新经验生存统计
8. 由本轮直接活动与模式补全活动更新旧边及其支持时间
9. 写入 H_t、过去→当前与当前→过去关系
10. 提交 BurstState_{t+1} 与 G_{t+1}
```

当前输入在读取时还不是图节点，因而不会通过自己刚写入的边产生本轮自反馈。检索会影响 LLM 回复，回复进入完整 turn 后又影响下一轮 dense、BM25 与共同活动；读取结果由此参与未来写入。

该反馈循环包含两种正反馈：

- 有意义的重叠情景在不同 query 中反复共同活动，membership 和可达路径增加；
- 偶然节点若后续不再与核心共同活动，其关系会在再次暴露时被 Oja-like 负项和连接预算压低。

第二点是系统希望实现的自净化机理。V2 还使长期未获支持的关系有效电导按经验复现生存函数下降，并让再次激活产生部分恢复。当前真实数据反事实实验已经观察到行为层噪音减少，但尚未证明错误 raw membership 会被删除，也未完成足够长、独立数据上的统计验证。

## 7. 实验

### 7.1 数据与重放协议

以下为原始 v8 实验数据。V2 的 parity 验证使用更大快照（4,858 turn），详见 §7.4。

v8 数据来自本机长期对话库的冻结副本。配对后得到：

- 4,828 个完整 user–assistant turn；
- 25 个 session；
- 主 Telegram stream 4,139 个 turn。

所有 session 进入同一长期图；活动 burst 仍按 stream 独立维护。重放严格按全局提交顺序进行。每一轮先检索、后学习，任何 query 都看不到未来 turn。

本研究选取 11 个研究过程中反复讨论、覆盖不同困难类型的真实 query：

- 医疗 storyline 与药物归因；
- 歌曲和动画评论；
- 发烧饮水；
- 两个睡眠问法；
- 插件重构与插件化；
- 比赛背景与比赛结果；
- 实习生 RAG；
- 求职经历总结。

人工 storyline 与噪音标注只用于评估和读出版本选择，没有进入在线图学习、seed、扩散或边更新。

### 7.2 指标

- **union hits**：当前可见 burst 与显式召回合并后命中的人工 storyline turn；
- **external hits**：显式召回补回的 burst 外目标 turn；
- **prior-burst anchor coverage**：每个需要旧情景的 storyline 是否至少恢复一个旧 burst 锚点；
- **reviewed noise hits**：命中有限人工噪音清单的数量；
- **recalled turns**：总显式 turn 数；
- **max/query**：单 query 最大显式 turn 数；
- **semantic digest**：去除运行时间后，对完整结果进行规范 JSON 哈希。

`reviewed noise hits` 只覆盖人工检查过的错误，不等同于全体结果的假阳性数，也不能据此计算精确率。

### 7.3 全量结果（v8 原始数据）

冻结库跨度约 154.7 天。9,432 个直接复现间隔的 25%、50%、75% 和 90% 分位分别为 0.410、52.836、272.160 和 741.212 小时。按时间前 70% 训练、后 30% 测试，候选分布的未来负对数似然为：

| 复现间隔模型 | 未来测试 NLL，nats/样本 |
|---|---:|
| 指数分布 | 16.61147 |
| 单 lognormal | 14.60372 |
| Lomax | 14.86906 |
| 双 lognormal mixture | **14.44136** |
| 三 lognormal mixture | 14.48065 |
| 四 lognormal mixture | 14.54139 |

双 lognormal 在未来数据上最佳；增加第三、第四分量反而变差。最终因果重放学到短复现中心 132.30 秒、长复现中心 400,761.12 秒（约 4.64 天）；1、7、30 天生存概率分别为 0.51949、0.25785 和 0.08738。这些数值描述当前聊天记录，不解释为人脑常数。

核心 V2 全量 rebuild 耗时 114.221 秒，峰值 RSS 为 400,152 KiB，得到：

| 项目 | 数值 |
|---|---:|
| turn | 4,828 |
| event hub | 4,654 |
| relation | 20,293 |
| 直接复现间隔 | 9,432 |
| 评估 query | 11 |
| union hits | 48 |
| external hits | 34 |
| 旧 burst 锚点 | 11 / 11 |
| 人工标记噪音命中 | 0 |
| 显式召回总数 | 159 |
| 单 query 最大 | 28 |

每个查询的读取规模如下：

| seq | 查询主题 | 可见 burst | 活动 basin | 显式 turn | external hits | union hits | 已标噪音 |
|---:|---|---:|---:|---:|---:|---:|---:|
| 7877 | 就医、头孢、荨麻疹 | 7 | 3 | 16 | 7 | 12 | 0 |
| 10306 | 少年少女 / Sonny Boy | 0 | 2 | 11 | 7 | 7 | 0 |
| 8566 | 发烧与冷热水 | 0 | 3 | 20 | 4 | 4 | 0 |
| 9224 | 昨晚睡得怎么样 | 1 | 1 | 5 | 1 | 1 | 0 |
| 8464 | 昨晚睡得咋样 | 0 | 2 | 13 | 2 | 2 | 0 |
| 9892 | 插件架构改造 | 0 | 5 | 28 | 3 | 3 | 0 |
| 4740 | 近期插件 / PR 延续 | 2 | 3 | 19 | 1 | 3 | 0 |
| 9624 | 比赛背景 | 1 | 3 | 15 | 4 | 5 | 0 |
| 9710 | 比赛结果 | 0 | 2 | 14 | 4 | 4 | 0 |
| 5294 | 实习生 RAG | 2 | 2 | 8 | 1 | 3 | 0 |
| 3011 | 轻舟已过万重山 | 4 | 2 | 10 | 0 | 4 | 0 |

### 7.4 读取消融

旧稳定版与 v8 的整体权衡为：

| 读取版本 | union | external | 旧 burst 锚点 | 已标噪音 | 总召回 | 单 query 最大 |
|---|---:|---:|---:|---:|---:|---:|
| v6：无经验遗忘的旧稳定版 | 50 | 36 | 11 / 11 | 3 | 248 | 38 |
| v8：经验生存 + 可达性竞争 | 48 | 34 | 11 / 11 | 0 | 159 | 28 |

V2 少命中 2 个 union 与 2 个 external 标注项，但保留全部旧 burst 锚点，同时减少 89 条显式召回、3 条有限噪音清单命中，并把最大单 query 从 38 降至 28。由于 storyline 和噪音标注都不完整，该表只能说明当前开发集上的覆盖—成本趋势，不能当成精确率或召回率。

真实反例说明改进来自遗忘作用位置，而不是简单加强衰减。对 `7877`：

| 机制 | 召回 | 活动 basin | 早期手感染命中 |
|---|---:|---:|---|
| effective weight 同时用于 basin 匹配和扩散 | 6 | 1 | 7471, 7494 |
| 完全关闭遗忘 | 31 | 5 | 7262, 7436, 7438, 7442, 7471, 7494 |
| raw 匹配、effective 扩散 | 27 | 5 | 保留完整早期链 |
| 再加相对可达性竞争 | 16 | 3 | 保留完整早期链 |

相同机制把 `实习生 RAG` 从 21 条压缩至 8 条，同时保留论文订阅、图 RAG、多跳和关系构建。它说明“engram 属于谁”和“目前还能传播多少”必须分开建模。

### 7.5 真实案例

**就医 query。** 当前 burst 已包含全身荨麻疹、连续服用头孢和氯雷他定等 7 个 turn。显式记忆额外恢复 16 个 turn，其中包含鱼石脂后疼痛与发热、皮下发白和流脓、吃头孢三天仍有脓、引流换药与出血、恢复期瘙痒，以及后续关节和膝关节瘙痒。人工 storyline 中恢复 7 个 burst 外目标 turn，合并当前 burst 后命中 12 个。相比 31 条无遗忘结果，它去掉了褪黑素、泛健康和写代码等旁支。

**“少年少女真好听”。** 当前没有可见历史 burst。系统激活 2 个旧 basin，返回 11 个 turn，恢复 7 个旧 storyline turn，包括 Sonny Boy、歌曲纠错、歌词、随机播放和既往音乐偏好。该案例说明短 query 可以通过旧情景 hub 形成链式补全。

**睡眠 query。** `9224` 只有 1 个活动 basin，排除 1 个当前可见 turn 后返回 5 个显式 turn。它没有像医疗或求职 query 一样扩大到几十条，说明候选数不是全局固定值。

**实习生 RAG。** 系统返回 8 个 turn，保留论文订阅、图 RAG、多跳与关系构建，未再命中预先审阅的游戏偏好、写作风格和泛焦虑噪音。

**“轻舟已过万重山”。** 当前求职 burst 中 4 个关键 turn 已经可见，因此这些内容不再作为显式记忆返回。系统恢复 10 个更早的疲惫、面试时间、焦虑和求职经历；相比旧版 38 条，prompt 压力显著降低。

**比赛歧义。** `9624` 返回 15 条，主要恢复公司比赛、数据治理、LD、Demo Night 和对大厂粗制滥造的评价。`9710` 返回 14 条，并恢复比赛结果前的公司故事，但仍包含少量 CS2 的“比赛”歧义。局部 write-only cause credit 消融将预先标注压力集里的 CS2 命中从 3 条降至 1 条，公司情景增加 1 条；10/11 个重点 query 的召回集合完全不变，所有人工 storyline 损失为 0。该结果只支持局部写入分离，尚不足以证明全局模式分离。

### 7.6 同 burst 噪音自净化

实验把一个完整、语义无关的“巨构建筑图片”turn 移入真实手感染 burst 的两个医疗 turn 之间。其时间间隔使当前 burst 分类器确实把它判为连续事件，因此不是不可达的人工状态。

| 分支 | 后续医疗查询中的最终噪音能量占比 |
|---|---:|
| V2：单次错误绑定，随后自然运行 | **1.26%** |
| 关闭经验遗忘 | 4.72% |
| 每轮强制重复错误激活 | 3.62% |

该无关节点第一次被错误绑定时占 4.97%。后续多个不完全重叠的医疗事件形成不包含该节点的新路径，V2 使它降到 1.26% 并退出最终 readout；关闭遗忘后几乎不下降。强制反复激活仍能保护这条错误关系，说明系统没有无差别清除频繁模式。

这个结果证明的是**行为路由自净化**，不是错误突触删除。最初受污染的固定 cue 仍可能沿原 hub 恢复该岔路，raw membership 也不保证单调下降。要证明更强的突触级自净化，需要让某条边自身产生的 recurrent activation 不能为同一条边提供完整 LTP 信用，并做独立消融。

### 7.7 确定性

相同冻结索引分别在 `PYTHONHASHSEED=0` 和 `PYTHONHASHSEED=731` 下独立全量 rebuild，两个 SQLite 数据库逐字节 SHA-256 均为：

```text
d1d51af3df845b776fc691180f76bf11cea5fbebe9091b4b6c1f7e5c36204b80
```

SQLite `quick_check=ok`，外键错误为 0；当前相关测试为 36 passed。实现对集合遍历、节点顺序、heap tie-break、SQLite 输出和 JSON 序列化采用稳定排序。该结果证明当前环境中两次独立 rebuild 逐字节一致，不证明不同 NumPy、BLAS、embedding 模型或硬件环境之间必然逐位一致。

### 7.8 与当前 v1 `ripple_items` 的结果对照

2026-07-27 对当前线上 `akasha.db` 做 SQLite 一致性只读快照，读取这些 query 在原始发生时保存的 `ripple_items` 和 `activation_items`；没有重新执行线上查询，也没有修改 live DB。v1 ripple 与 v8 显式 completion 的数量为：

| seq | 查询主题 | v1 ripple | v8 completion |
|---:|---|---:|---:|
| 3011 | 轻舟已过万重山 | 8 | 10 |
| 4740 | 插件 / PR 延续 | 9 | 19 |
| 5294 | 实习生 RAG | 8 | 8 |
| 7877 | 就医 / 头孢 / 荨麻疹 | 9 | 16 |
| 8464 | 昨晚睡得咋样 | 16 | 13 |
| 8566 | 发烧喝水 | 8 | 20 |
| 9224 | 昨晚睡得怎么样 | 16 | 5 |
| 9624 | 比赛是啥 | 8 | 15 |
| 9710 | 比赛结果 | 8 | 14 |
| 9892 | 插件系统重构 | 12 | 28 |
| 10306 | 少年少女真好听 | 8 | 11 |

数量不是质量本身。逐条内容显示：v1 就医结果混入上腹疼痛和麦当劳，发烧结果混入地铁、咖啡和通勤，睡眠结果包含大量近义重复；V2 能恢复更完整的医疗、感冒和公司比赛叙事，并将 `9224` 压缩到 5 条。v1 的 Sonny Boy 结果已有歌曲联想，但 8 条中夹有 3 条明显游离内容；V2 的 11 条扩展到歌词纠正、随机播放和既往音乐偏好。

在这 11 个 v1 query 中，83 个 activation item 有 70 个也出现在 ripple 中，重合率为 84.3%。这说明 v1 activation 大部分在重新学习当前 ripple 池；错误 ripple 也可能进入后续关系。V2 的优势因此不是单独换候选表，而是同时替换联想读取与激活学习责任。

该对照不是严格 A/B：v1 行来自历史 query 当时的图状态和旧 prompt 预算，V2 行来自冻结历史的因果重放。它足以展示真实功能差异，不足以代替同一在线前缀上的 replay–online parity。

## 8. 机理分析

### 8.1 系统已经具备什么联想

联想发生在三个层次：

1. **线索联想**：dense 和 BM25 从当前 query 找到少量历史入口；
2. **情景联想**：入口通过共同激活 hub 恢复同一事件的其他成员；
3. **顺序联想**：不对称时间边从起因传播到后果，也允许较弱的反向回忆。

因此，`a` 在第一次出现时形成稀疏事件，`b` 到来时若召回 `a`，本轮 hub 会绑定 \(a,b\)；`c` 只命中 `b` 时，仍可沿 \(b\rightarrow H_{ab}\rightarrow a\) 完成 \(a\) 的恢复，再将 \(a,b,c\) 写入新的共同活动。重放状态机已经实现这条链式路径。

### 8.2 为什么不会无限强化

五个因素共同约束正反馈：

- 传播每步有 restart 泄漏，固定点唯一；
- hub、turn 来源侧 membership 与时间出边有连接预算；
- Oja-like 更新包含权重相关负项；
- 短时间反复刺激会消耗资源并提高可塑性阈值。
- 长期未获支持的关系只按经验生存函数提供有效电导，召回也只产生部分恢复。

这些机制分别约束“本轮活动是否发散”和“长期边权是否无界”。它们不能保证图永远没有错误吸引域，因为错误 seed 仍可能被写入新 hub。

### 8.3 \(abc/e\) 自净化假设

设第一次召回为 \(\{a,c,e\}\)，第二次同主题召回为 \(\{a,b,c\}\)。若后续线索经常共同激活 \(a,b,c\)，则这些节点参与更多重叠 hub 和时间路径；\(e\) 只存在于少数早期 hub。旧 hub 再次暴露时，\(e\) 活动较低，Oja-like 项可能压低其 membership；局部预算又把质量分配给持续共同活动成员。

该机理与竞争 Hebbian 学习和检索后记忆更新的研究结果一致 [7, 18–20]。V2 不被动删除 \(e\) 的原始 membership，但长期未获支持的路径会降低有效电导；相关情景再次出现时，不含 \(e\) 的重叠 hub 还会提供新的竞争路径。真实医疗干预中，\(e\) 的能量从 4.97% 降至 1.26% 并退出 readout，而关闭经验遗忘后仍为 4.72%。因此更准确的主张是：

> 经验可达性与后续重叠情景会降低未获支持噪音对新 query 的路由贡献；它们不保证原始错误边被删除，也不保证最初受污染 cue 永远无法恢复该岔路。

### 8.4 机理清晰度

当前系统的算法对象已经清楚：

- turn 是原子记忆；
- burst 是 stream-local 工作事件；
- seed 是 dense、BM25、时间和 burst 的稀疏证据；
- hub 是共同激活的因子化记录；
- 时间边表达顺序；
- Residual Push 定义查询钳制稳态；
- Oja-like 更新、双侧预算、资源和阈值定义短期可塑性；
- 经验复现生存与部分支持恢复定义长期可达性。

仍不清楚或尚未证明的部分包括：

- 连续性信念是否在新用户、新语言和新 embedding 上保持校准；
- sparsemax 后不同事件是否满足严格模式分离；
- direct 与 recurrent activation 应如何分配关系更新和支持恢复责任；
- 经验复现分布在话题、工具任务和情绪事件之间是否需要条件化；
- 少数手工 query 上观察到的自净化趋势能否扩展到长期无标注流。

因此，该机理足以支撑系统论文和在线原型，尚不足以支撑“复现海马”或“满足 AGI 显式记忆”的强主张。

## 9. 机制覆盖与理论边界

### 9.1 对 Park 显式记忆要求的覆盖

| 计算要求 [1] | 当前状态 | 证据与缺口 |
|---|---|---|
| 稀疏索引 | 部分满足 | sparsemax / entmax 产生有限支持；turn 仍是原子索引，不是学习出的稀疏神经字典 |
| 非误差驱动更新 | 满足 | 图更新不依赖答案 loss、用户标签或梯度 |
| 关联构造 | 满足 | 共同激活 hub 与有向时间边 |
| 模式分离 | 局部初步证据 | write-only cause credit 在公司/CS2 压力流中将 3 条竞争命中降至 1 条且无 storyline 损失；缺少全局 dense→sparse 相似度非扩张检验 |
| 模式补全 | 初步满足 | 10 个具有标注旧情景的 query 覆盖 11 个旧 burst，均恢复锚点 |
| 动态性 | 满足 | 每轮 read-before-write 后图状态变化 |
| 单次快速可塑性 | 工程上满足 | 新 turn 在提交后即可影响下一轮；缺少专门 one-shot 基准 |
| 适应性遗忘 | 初步满足 | 直接复现间隔学习生存函数，未获支持关系有效电导下降，召回后部分恢复；单一用户与行为层自净化证据仍有限 |

### 9.2 自适应读取的边界

RF-Mem 根据证据不确定度切换读取方式 [2]。Akasha 借用了“不确定线索需要扩大读取范围”这一观察，并用连续 basin 温度表达读取范围。Akasha 的核心状态仍是历史共同活动形成的持久图；RF-Mem 的聚类中心只在当前读取过程中改写查询。这里的引用用于说明读取范围为何需要自适应，不构成 Akasha 的主体方法。

### 9.3 边权状态的边界

Memini 用多时间尺度边表达巩固与遗忘 [3]。Akasha V2 采用原始单一边权与经验有效电导的显式分解：资源和阈值调节可塑性，双 lognormal 生存调节读取，支持时间表达连续再激活。该选择让状态可以直接检查，但仍没有 Benna–Fusi 耦合隐藏变量提供的长期容量理论；经验生存曲线描述“本用户何时再次直接命中”，也不等价于记忆价值或生物保持曲线。

## 10. 在线实验与 v1 替换边界

### 10.1 结论

Akasha V2 **已达到独立显式记忆引擎的交付门槛**。

核心交付已经完成：11 个冻结 query 与 V8 召回集合逐条一致（零差异）；在线与重放 logical state hash 相同；Message feedback 实现定向 remember/forget 控制且不改变图拓扑。17 次 remember 的 Top-1 保持率 99.55%，7 次 forget 将错误目标召回率从 3.10% 降为 0。

已交付能力包括：

- 因果 per-turn 状态机（MemoryCycle）运行在正式 runtime API；
- 在线稀疏索引增量构建（build_sparse_index）；
- SQLite WAL 下的原子图持久化与版本管理；
- stream-local burst 状态持久化和崩溃恢复；
- replay–online parity 已验证（同一 source index 得到相同 logical state）；
- writer lease 单写者并发控制；
- Message feedback staging + persistence 全链路集成。

当前部署状态：V2 以独立 sidecar 运行，与 V1 DB 共存。以下为 V2 的运行时架构：

```text
                         ┌─ dense 精确语义召回 ───────┐
query ── 稀疏证据 ───────┤                            ├─ 去重 ─→ LLM
                         └─ V2 completion / storyline (两路读出) ┘
                                      │
                                      ▼
                         V2 activation + feedback 抑制/增强 + 部分支持恢复
```

dense 可以继续承担隐式语义模式的精确入口；V2 同时替换 v1 ripple 的图联想和 activation 的写回责任。上线时应新建 V2 sidecar，使它成为独立显式记忆主引擎，同时保留 v1 DB 为只读回滚点。

### 10.2 在线事务边界

建议的最小原子事务为：

```text
┌──────────────────────────────────────────────┐
│ 输入：stream_id, user_message_id, user_text  │
├──────────────────────────────────────────────┤
│ 只读快照：graph_version = v                  │
│ 推断：burst、seed、recall、provenance        │
│ 生成：assistant response                     │
│ 组装：完整 turn                              │
│ 写入：turn + edge delta + burst state        │
│ 提交：graph_version = v + 1                  │
└──────────────────────────────────────────────┘
```

同一 `user_message_id` 重试必须幂等；提交失败时不能只保存 assistant 或只保存半个图更新。

### 10.3 上线前缺口

| 项目 | 当前状态 | 上线前要求 |
|---|---|---|
| 因果 per-turn 状态机 | replay 已验证 | 移入正式 runtime API |
| 稀疏索引 | 离线冻结库生成 | 在线生成 dense、词项和 gap |
| 图持久化 | rebuild 后整库写出 | SQLite WAL 下的增量事务与版本 |
| burst 状态 | 内存 tracker | 每 stream 持久状态和崩溃恢复 |
| 经验复现与支持恢复 | replay 状态已持久化 | 在线每 turn 原子更新及重启恢复 |
| v1 结果对照 | 历史 query log 已逐条审阅 | 相同冻结前缀下双引擎 parity |
| 并发 | 未定义 | stream 内串行、跨 stream 冲突策略 |
| 幂等 | 未实现 | message/turn 唯一键与重复提交检测 |
| 延迟 | 核心 rebuild 平均约 24 ms/turn | 全查询读出 p50/p95/p99 与峰值 push |
| prompt 集成 | 报告层排除 visible burst | 接入真实 `visible_message_ids` 和 token 预算 |
| 可观测性 | target query 保存来源 | 所有线上 query 保存 seed、路径、版本和 residual |
| 数据治理 | 本地私有实验 | 删除、导出、访问控制、embedding 派生数据清理 |
| 模型升级 | 未定义 | embedding/BM25 版本迁移与双读验证 |

114.221 秒除以 4,828 约为 24 ms/turn，但该值不能当作线上读取延迟：全量 replay 共享内存特征矩阵，而且只有 11 个 target 执行完整 basin 读出。线上每个 query 都执行读出后，延迟会更高，必须单独压测。

## 11. 局限与有效性威胁

### 11.1 数据范围

实验来自单一用户的私有长期对话。语言风格、消息节奏、主题重复和 assistant 行为高度个体化。结果不能直接外推到多用户或企业知识库。

### 11.2 评估集

11 个 query 是开发过程中持续关注的案例，既参与设计反馈，也参与最终版本选择。它们可以用于机制回归，不是独立测试集。后续需要冻结开发集，再建立未见 query、未见时间段和跨用户测试。

### 11.3 标签不完整

storyline 只标注少量关键 turn，人工噪音表更不完整。`11/11 anchors` 表示每个已标旧情景至少命中一个锚点，不表示返回的 159 个 turn 都有用。

### 11.4 读取与学习耦合

检索活动进入下一轮学习，能产生联想，也会产生算法自混杂：过去的错误召回提高未来再次召回的机会。连接预算、元可塑性和经验遗忘降低该风险，没有从理论上消除它。V2 已分别记录 direct seed 的 independent credit 与图传播的 recurrent credit，但核心支持恢复仍使用总共同激活信用；这留下“关系能否用自身产生的活动自我续命”的可达反馈路径。推荐系统研究也表明，系统输出影响后续数据时，离线相关性会被反馈回路混淆 [17]。

### 11.5 生物解释

事件边界、STDP、元可塑性和突触资源为设计提供计算启发。工程中的分钟级 turn、图边和 soft posterior 不能直接解释为神经元放电、真实突触或海马亚区功能。

### 11.6 经验遗忘的外推边界

V2 已实现随时间下降的有效可达性，但它学习的是本用户、当前 embedding 与当前 BM25 下的直接复现间隔，不是记忆价值、事实正确性或生物保持率。双 lognormal 分量由在线距离分配形成，没有独立校准；当用户习惯、定时任务比例或 embedding 模型改变时，生存曲线可能漂移。

原始边权不会仅因时间流逝而删除，因此旧 hub 仍占用存储，强 direct cue 也可能重新选择它。这个设计允许“很久没想起但仍能被准确线索恢复”，代价是需要另行处理数据库压缩、被遗忘数据删除和模型迁移。行为层自净化不能被表述成数据物理删除。

## 12. 后续研究

1. 将 burst-aware 机制移入正式 runtime，建立在线—replay 逐 turn parity 测试；
2. 冻结独立 query 集，增加跨时间段、跨用户和反事实噪音实验；
3. 直接测量模式分离：

$$
\operatorname{sim}(S_i,S_j)
<
\operatorname{sim}(E_i,E_j)
$$

在不同事件但语义相似的样本上是否成立；

4. 在已完成的医疗 \(abc/e\) 行为实验上增加独立证据信用消融，验证 recurrent activation 是否会让原始错误边自我续命；
5. 比较全局经验生存、按事件类型条件化的生存函数和 Benna–Fusi 多时间尺度状态；
6. 将 direct dense/BM25 通道与显式 completion 并行交给 LLM，做内容去重和来源分栏；
7. 用回答效用评估显式记忆是否给最终 LLM 增加了当前上下文中缺失的有效信息。

## 13. 结论

Akasha V2 已经超过“给每个 turn 携带 dense、BM25 和时间字段”的结构体阶段。它把线索投影成稀疏活动，在历史共同激活形成的图上完成局部扩散，再把本轮活动写回图中；经验复现生存让未获支持的关系逐渐降低有效电导，部分支持恢复又使反复有意义的情景保留可达性。全量回放结果表明，系统能够从短线索恢复旧歌曲情景，从荨麻疹问诊回到更早的手指感染与就医过程，也能让简单睡眠查询保持较小的输出范围。

机理最清楚的部分是因果状态机、因子化 hub、有向时间边、残余扩散、双侧局部竞争，以及 raw 结构与 effective 可达性的分离。局部比赛压力实验给出了模式分离的初步证据，真实医疗噪音干预给出了行为层自净化证据，但全局模式分离、突触级错误删除和跨用户遗忘校准仍未证明。

以系统研究和召回效果判断，V2 已经是一套可审计、可消融、可重复、已通过首次交付验收的独立显式记忆引擎。在线增量事务、崩溃恢复、replay–online parity 和 Message feedback 均已实现并验证。11 个冻结 query 与 V8 逐条一致；双路读出、双 lognormal 生存和 feedback 机制为后续研究提供了清晰的可消融基线。当前正确动作是以 V2 为显式记忆主引擎，V1 保留为只读回滚点。

## 参考文献

[1] Park, S. 2026. *Position: Hippocampal Explicit Memory Is the Cornerstone for AGI*. [arXiv:2606.11245](https://arxiv.org/abs/2606.11245). 仓库全文：[Markdown](./2606.11245-hippocampal-explicit-memory-cornerstone-agi.md).

[2] Zhang, Y., Li, J., Zhang, W., et al. 2026. *Evoking User Memory: Personalizing LLM via Recollection–Familiarity Adaptive Retrieval*. ICLR 2026. [arXiv:2603.09250](https://arxiv.org/abs/2603.09250); [OpenReview PDF](https://openreview.net/pdf/299e700211a41d6876a8d790ce8ee11b530df900.pdf).

[3] Pattichis, A., & Dovrolis, C. 2026. *Continual Knowledge Updating in LLM Systems: Learning Through Multi-Timescale Memory Dynamics*. [arXiv:2605.05097](https://arxiv.org/abs/2605.05097). 仓库全文：[Markdown](./2605.05097-continual-knowledge-updating-multi-timescale-memory.md).

[4] Pu, Y., Kong, X.-Z., Ranganath, C., & Melloni, L. 2022. Event boundaries shape temporal organization of memory by resetting temporal context. *Nature Communications*, 13, 622. [DOI:10.1038/s41467-022-28216-9](https://www.nature.com/articles/s41467-022-28216-9).

[5] Zheng, J., Schjetnan, A. G. P., Yebra, M., et al. 2022. Neurons detect cognitive boundaries to structure episodic memories in humans. *Nature Neuroscience*, 25, 358–368. [DOI:10.1038/s41593-022-01020-w](https://www.nature.com/articles/s41593-022-01020-w).

[6] Bakker, A., Kirwan, C. B., Miller, M., & Stark, C. E. L. 2008. Pattern Separation in the Human Hippocampal CA3 and Dentate Gyrus. *Science*, 319(5870), 1640–1642. [DOI:10.1126/science.1152882](https://pmc.ncbi.nlm.nih.gov/articles/PMC2829853/).

[7] Song, S., Miller, K. D., & Abbott, L. F. 2000. Competitive Hebbian learning through spike-timing-dependent synaptic plasticity. *Nature Neuroscience*, 3, 919–926. [DOI:10.1038/78829](https://www.nature.com/articles/nn0900_919).

[8] Oja, E. 1982. Simplified neuron model as a principal component analyzer. *Journal of Mathematical Biology*, 15, 267–273. [DOI:10.1007/BF00275687](https://doi.org/10.1007/BF00275687).

[9] Abraham, W. C., & Bear, M. F. 1996. Metaplasticity: the plasticity of synaptic plasticity. *Trends in Neurosciences*, 19(4), 126–130. [DOI:10.1016/S0166-2236(96)80018-X](https://pubmed.ncbi.nlm.nih.gov/8658594/).

[10] Benna, M. K., & Fusi, S. 2016. Computational principles of synaptic memory consolidation. *Nature Neuroscience*, 19, 1697–1706. [DOI:10.1038/nn.4401](https://www.nature.com/articles/nn.4401).

[11] Martins, A. F. T., & Astudillo, R. F. 2016. From Softmax to Sparsemax: A Sparse Model of Attention and Multi-Label Classification. *ICML 2016*. [PMLR 48:1614–1623](https://proceedings.mlr.press/v48/martins16.html).

[12] Chen, Z., Guo, X., Zhou, B., Yang, D., & Skiena, S. 2023. Accelerating Personalized PageRank Vector Computation. [arXiv:2306.02102](https://arxiv.org/abs/2306.02102).

[13] Wei, Z., Wen, J.-R., & Yang, M. 2024. Approximating Single-Source Personalized PageRank with Absolute Error Guarantees. [arXiv:2401.01019](https://arxiv.org/abs/2401.01019).

[14] Peters, B., Niculae, V., & Martins, A. F. T. 2019. Sparse Sequence-to-Sequence Models. *ACL 2019*, 1504–1519. [DOI:10.18653/v1/P19-1146](https://aclanthology.org/P19-1146/).

[15] Schiller, D., Monfils, M.-H., Raio, C. M., Johnson, D. C., LeDoux, J. E., & Phelps, E. A. 2010. Preventing the return of fear in humans using reconsolidation update mechanisms. *Nature*, 463, 49–53. [DOI:10.1038/nature08637](https://www.nature.com/articles/nature08637).

[16] Abbott, L. F., Varela, J. A., Sen, K., & Nelson, S. B. 1997. Synaptic depression and cortical gain control. *Science*, 275, 220–224. [DOI:10.1126/science.275.5297.221](https://doi.org/10.1126/science.275.5297.221).

[17] Chaney, A. J. B., Stewart, B. M., & Engelhardt, B. E. 2018. How Algorithmic Confounding in Recommendation Systems Increases Homogeneity and Decreases Utility. *RecSys 2018*. [DOI:10.1145/3240323.3240370](https://doi.org/10.1145/3240323.3240370).

[18] Nader, K., Schafe, G. E., & LeDoux, J. E. 2000. Fear memories require protein synthesis in the amygdala for reconsolidation after retrieval. *Nature*, 406, 722–726. [DOI:10.1038/35021052](https://www.nature.com/articles/35021052).

[19] Detre, G. J., Natarajan, A., Gershman, S. J., & Norman, K. A. 2013. Moderate levels of activation lead to forgetting in the think/no-think paradigm. *Neuropsychologia*, 51(12), 2371–2388. [DOI:10.1016/j.neuropsychologia.2013.02.017](https://doi.org/10.1016/j.neuropsychologia.2013.02.017).

[20] Wimber, M., Alink, A., Charest, I., Kriegeskorte, N., & Anderson, M. C. 2015. Retrieval induces adaptive forgetting of competing memories via cortical pattern suppression. *Nature Neuroscience*, 18, 582–589. [DOI:10.1038/nn.3973](https://www.nature.com/articles/nn.3973).

## 附录 A：实现与实验材料

| 材料 | 路径 |
|---|---|---|
| 因果特征与 seed | [`src/akasha/domain/features.py`](../../src/akasha/domain/features.py) |
| 单状态图与可塑性 | [`src/akasha/domain/graph.py`](../../src/akasha/domain/graph.py) |
| Residual Push | [`src/akasha/domain/diffusion.py`](../../src/akasha/domain/diffusion.py) |
| read-before-write 重放 | [`src/akasha/application/cycle.py`](../../src/akasha/application/cycle.py) |
| 双路读出与 basin 补全 | [`src/akasha/domain/readout.py`](../../src/akasha/domain/readout.py) |
| Message feedback 与 engine adapter | [`src/akasha/engine.py`](../../src/akasha/engine.py) |
| 在线 runtime 与 staging | [`src/akasha/application/runtime.py`](../../src/akasha/application/runtime.py) |
| 全量 rebuild 重放 | [`src/akasha/application/rebuild.py`](../../src/akasha/application/rebuild.py) |
| SQLite 持久化与 schema | [`src/akasha/infrastructure/persistence.py`](../../src/akasha/infrastructure/persistence.py) |
| 验证与验收报告 | [`docs/validation.md`](../validation.md) |

## 附录 B：主张登记

| 类型 | 本文主张 |
|---|---|
| 代码事实 | 重放严格 read-before-write；seed、hub、时间边、双侧预算、资源、阈值、经验生存、部分支持恢复和 residual 均已实现 |
| 数学事实 | 固定 query 下的带重启非负转移存在唯一固定点；reserve 是逐步增加的下界 |
| 实验观察 | 10 个具有标注旧情景的 query 共覆盖 11 个旧 burst，均恢复锚点；噪音干预能量 4.97%→1.26%；两种 hash seed 的 SQLite 逐字节一致 |
| 有限推断 | 双路读出改善了短句独立寻址与强 cue 抗噪声的权衡；feedback 实现定向控制且不改变图拓扑；V2 已通过首次交付验收 |
| 工程交付 | 在线增量事务、崩溃恢复、replay–online parity、Message feedback staging/persistence、writer lease 单写者并发 |
| 未证假设 | 长期无监督运行会普遍自净化；当前稀疏码满足全局严格模式分离；机制与海马生物实现等价 |

## 附录 C：结构标注稿

这一附录记录正文关键句的写作职责，便于后续改成正式投稿稿。

| 正文位置 | 关键句骨架 | 作用 |
|---|---|---|
| 摘要开头 | 长期对话需要恢复经历；单次检索与全上下文各有缺口 | 交代问题与现有方法缺口 |
| 摘要方法 | 系统以 turn 编码、burst、hub、经验生存和 residual diffusion 形成读写循环 | 压缩方法对象 |
| 摘要结果 | 真实重放规模、覆盖、噪音和确定性 | 给出可核验结果 |
| 引言研究问题 | 无监督条件下，关联图能否从检索活动中长出 | 明确可证伪问题 |
| 方法总览 | 输入线索生成 seed，图扩散读取，assistant 后写入新关系 | 建立因果顺序 |
| 机理边界 | 行为路由可以变干净，但 raw 错误边不保证删除 | 防止夸大自净化 |
| 在线结论 | 召回质量可进入实验；runtime parity 与事务边界尚未完成 | 区分实验性引擎与生产替换 |
| 结论 | 已观察到跨 burst 补全、局部分离和行为自净化；全局证明仍待完成 | 收束贡献与限制 |
