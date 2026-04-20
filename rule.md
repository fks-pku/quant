AI 编程大项目的协作框架设计：边界清晰、低耦合迭代
AI 辅助编程（Claude Code、Cursor 等）在大项目中最容易出现的反模式是：改 A 功能时牵动 B，修 B 引入 C 的回归。本质原因不是 AI 不够聪明，而是项目本身没有为 AI 提供 "可识别的边界"。下面给一套我实际使用有效的协作框架。
一、核心思路：把 "人和 AI 的协作" 当成分布式系统来设计
传统工程中，模块边界是为了降低人的认知负荷；AI 协作中，模块边界还要降低上下文窗口的污染半径。每次 AI 介入，它能看到、能修改的范围必须被显式约束。
三条底层原则：
Context Isolation（上下文隔离）：一次任务只让 AI 看到它需要的代码
Contract First（契约先行）：模块之间通过显式接口通信，AI 改实现但不改契约
Blast Radius Control（爆炸半径控制）：任何一次改动可影响的文件范围提前划定
二、代码层架构：面向 AI 的分层与目录约定
1. 强制纵向切分 + 垂直切片（Feature Slice）
传统分层（controller /service/dao）对 AI 不友好，因为一个功能的代码散落在多个水平层里，AI 改一处容易忘一处。推荐：
src/
├── features/              # 垂直切片，每个功能自成一体
│   ├── order/
│   │   ├── api.ts         # 对外契约（类型 + 函数签名）
│   │   ├── service.ts     # 业务逻辑
│   │   ├── repo.ts        # 数据访问
│   │   ├── __tests__/
│   │   └── CLAUDE.md      # 该 feature 的上下文说明
│   ├── payment/
│   └── user/
├── shared/                # 跨 feature 的纯工具（无业务语义）
└── platform/              # 基础设施（日志、DB、MQ 客户端）
关键点：feature 之间只能通过 api.ts 通信，禁止跨目录 import 内部文件。这条规则用 ESLint no-restricted-imports 或 dependency-cruiser 强制落地。
2. 每个 feature 配一个 CLAUDE.md
这是 AI 协作的关键资产，相当于 "模块说明书"：
# Order Feature

## 职责
处理订单创建、状态流转、与支付的协作

## 对外契约（禁止改动签名，需改动请先讨论）
- createOrder(input: CreateOrderInput): Promise<Order>
- getOrderById(id: string): Promise<Order | null>

## 依赖
- features/payment (仅通过 api.ts)
- platform/db

## 不变量
- 订单状态机：DRAFT → PAID → SHIPPED → DONE，不可逆
- 金额单位：分（integer），不使用 float

## 修改守则
- 改业务逻辑：只动 service.ts
- 改数据结构：必须同步更新 api.ts 类型并跑 `pnpm typecheck`
- 新增状态：更新状态机图 + 补充测试
让 AI 每次进入 feature 时先读这份文件，出错率立刻下降。
三、任务流程：从 "整包扔给 AI" 到 "结构化协作"
1. 四段式 Prompt 模板
阶段	产出	AI 权限
Plan	列出将要改的文件、接口变化、风险点	只读，不改代码
Confirm	人工 review plan，修正边界	—
Execute	按 plan 改代码 + 写测试	限定文件范围
Verify	跑测试、lint、typecheck	只读
实操中强制让 AI 先输出 Plan（如 Claude Code 的 Plan Mode），不 approve 不准动代码。这一步能拦截 70% 的越界改动。
2. 约束 "可修改范围"
发任务时明确告诉 AI：
本次任务只允许修改 src/features/order/**，如需改动其他目录请停下来说明原因。
配合 git pre-commit hook 检查 diff 范围：
# 简化示例：任务标签为 order 时，禁止 diff 溢出到其他 feature
git diff --name-only --cached | grep -v '^src/features/order/' | grep '^src/features/' && exit 1
四、接口契约：让耦合 "显式化"
1. 类型即契约
TypeScript / Python Pydantic / Protobuf 都行，关键是：
跨 feature 调用只能通过类型化的接口
接口变更走 "扩展而非修改"（加可选字段而非改已有字段）
用 breaking-change label 标记破坏性变更，强制走 code review
2. 事件驱动解耦
对于 A 完成后 B、C 要联动的场景，不要让 A 直接调 B、C，而是发事件：
// order/service.ts
eventBus.emit('order.paid', { orderId, amount })

// payment/subscriber.ts 和 notification/subscriber.ts 各自订阅
这样改 A 的时候，B/C 的代码和 AI 上下文都不会被打开，真正做到 "改 A 不影响 B"。
五、测试与回归：AI 的 "安全网"
AI 最怕无声回归。配套三件套：
单元测试覆盖契约层：每个 api.ts 导出的函数必须有测试，改契约必然挂测试
黄金路径集成测试：每个 feature 至少 1 条端到端主流程用例
快照测试兜底 UI / 序列化输出：AI 误改格式能被立刻发现
让 AI 遵循 TDD 变体：改代码前先补测试 → 测试先红 → 再改实现 → 测试转绿。这是对抗 "看似修好了其实破坏了别处" 的最有效手段。
六、上下文管理：让 AI 永远 "看到刚好需要的"
问题	对策
项目太大，AI 读不完	根目录 CLAUDE.md 只写全局约定 + feature 索引，子目录 CLAUDE.md 写局部细节
AI 反复踩同一坑	在对应 CLAUDE.md 加 "Known Pitfalls" 段落，把教训沉淀下来
改动需要跨 feature	显式列出涉及的 feature，让 AI 依次进入每个 feature 的上下文
历史改坏又改回	用 decisions/ 目录存放 ADR（架构决策记录），AI 改之前先读
七、迭代节奏：小步、可逆、可验证
一次一 feature，一次一 PR：禁止 AI 一次改多个 feature
PR 模板强制说明：变更范围、风险、回滚方式
feature flag 兜底：新功能默认关闭，线上出问题一键回滚而非紧急 hotfix
Trunk-based + 短命分支：避免长期分支里 AI 积累几百个文件的改动难以 review
八、一张速查表
症状	根因	对策
改 A 挂了 B	跨 feature 直接 import 内部文件	ESLint 边界规则 + 只暴露 api.ts
AI 改完没测试	没强制 TDD	Plan 阶段要求列出测试清单
反复在同一处出 bug	没沉淀约束	feature 级 CLAUDE.md 加 Pitfalls
契约悄悄变了	没区分 "扩展" 和 "破坏"	类型审查 + breaking-change label
AI 上下文污染	一次塞太多	垂直切片 + 分层 CLAUDE.md
改动回归难发现	测试覆盖薄	契约测试 + 黄金路径集成测试
九、落地顺序建议
如果现在是零起点，按优先级逐步引入：
根目录 + 每个核心模块写 CLAUDE.md（1 天）
跑 dependency-cruiser / ESLint 边界规则（半天）
强制 Plan → Confirm → Execute 流程（立刻生效）
给高频改动的 feature 补契约测试（1 周）
引入事件总线拆解强耦合调用（按需重构）
先做前 3 条，迭代时 AI 搞砸的频率会显著下降；后 2 条是长期收益。
核心一句话：AI 编程出 bug 多的项目，不是 AI 的问题，而是项目对 AI 的 "可观测性" 和 "可约束性" 不够。把边界写在代码里、把约束写在文件里、把流程写在 hook 里，AI 才会变成一个靠谱的协作者。
