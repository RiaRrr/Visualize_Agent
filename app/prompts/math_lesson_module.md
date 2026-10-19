## 输出为教学模块，由本地 SVG 框架组装完整 HTML
本次图片已直接提供。根据图片独立解题，最终只输出 JavaScript：`window.LESSON = { ... };`。不要输出 HTML、Markdown 围栏、计划或文件路径。此约定替代完整 HTML 输出；框架已负责页面、SVG、控件和事件，不要重新创建或查询 DOM，不使用外部库、网络、eval、定时器。不要输出隐藏思维链；模块中展示的是学生需要的精简解题依据。

模块契约（函数可以写在对象中；建议 4–7 步、1–2 个参数）：
```
window.LESSON = {
  title: '本题的教学标题',
  intro: '准确、简短的题设与各小问；不要省略关键约束',
  params: [{key:'u', label:'控制量及其来源', min:0.05, max:0.95, step:0.01, value:0.4,
            introducedAt:2, note:'完整数学定义域、演示范围与不可取边界'}],
  steps: [
    {title:'题设', focus:'这一步让学生观察的关系'},
    {title:'条件转化', focus:'这一关系如何转成计算所需的量'}
  ],
  // 允许一个固定 view 数组或 view(d,step) 函数；始终等比例显示数学坐标
  view: [-3,3,-2,2],
  compute(p) { return { /* 全部数学坐标、当前量和结果，由 p 独立计算 */ }; },
  draw(g,d,step) { /* 每步只画必要的题设对象与辅助构造 */ },
  explain(d,step) { return '<p>简短依据</p><div class="formula">核心公式</div>'; },
  // 可选局部图/函数图；与主图共用 d 和 step，在说明卡旁显示
  inset: {title:'局部关系/目标量变化', kind:'plot', fromStep:2, view:[0,1,0,2], draw(g,d,step){}},
  // 可选的取等位置/典型状态按钮；只能在推导完成后开放，不能用数值扫描冒充证明
  presets: [{label:'观察已推导的取等状态', fromStep:4, values:{u:0.5}}]
};
```
上面只是 API 形状示例，数值、步数、参数和函数体必须按真实题目独立确定，不能照搬。
**步骤索引严格从 0 开始。** 页面“第 1 步”传入的 step=0，最后一步传入 steps.length−1。draw(g,d,step)、explain(d,step)、inset.draw(g,d,step)、introducedAt 和 fromStep 全部使用这个相同索引。必须为 step=0 编写对应内容，禁止把默认 else 当作最终答案而漏掉初始步骤。compute(p) 中 p 只含已声明参数，不含 step。draw/explain 只能从 d 读取派生点和量，例如 d.P 或 `const {P,N}=d`，不能把未声明的 P/N 当成全局变量。每一个 steps 项都要在 draw/explain 中有对应的有效分支。
框架自动实现前后步骤、步骤导航、合法区间内的滑块、显示当前参数值、复位和预设状态。控件在 `introducedAt` 步才出现，必须安排在该步已经解释变量来源之后；未在题设直接定义的斜率/参数不能在首屏出现。首屏应有原题对象和约束，不提前给出答案；各步高亮改变主图。单纯改变 explain 文本而 draw 不体现步骤，不满足教学目标。

绘图 g API，所有点均用数学坐标数组 `[x,y]`，不要自行算像素：
- `g.axes()` 画坐标轴；`g.line(A,B,color='#64748b',width=2,dash='')` 画线段。
- `g.ellipse(center,rx,ry,color='#0f766e')` 画解析椭圆；`g.circle(center,r,color)` 同理。
- `g.path(points,color='#0f766e',width=2,dash='')` 画曲线采样点数组；`g.polygon(points,fill='#dbeafe',stroke='#60a5fa')` 画面积区域。
- `g.point(P,'点名',color='#0f766e',dx=10,dy=-12)` 画点和标签，dx/dy 是屏幕标签偏移；`g.text(P,'短标签',color='#334155',dx=0,dy=0)` 画文字。
- `g.segmentLabel(A,B,'长度或差值',color='#334155',offset=14)` 在两点中间偏移标注。
- 函数图也可以用这些 API 画，但要清楚标轴的变量及刻度；几何辅助图始终等比例。
主图 view 应紧包围实际图形与必要标签，留适量边距，避免过大的坐标范围让题图缩在大片空白中。inset.kind='plot' 的横纵轴分别缩放以充分展示变化，必须标明变量与刻度；局部几何图设置 kind='geometry' 保持等比例，不能用非等比图证明几何角度或长度。
颜色建议：题设/主对象青色 `#0f766e`，新辅助关系橙色 `#d97706`，当前目标与取等红色 `#be4452`；保持对象与公式同色。

可直接使用 MathKit 通用数学工具（不含本题答案）：
- `MathKit.slope(A,B)`；`MathKit.area(A,B,C)`；`MathKit.midpoint(A,B)`；`MathKit.centroid([A,B,C])`；`MathKit.axisY(A,B)` 返回直线 AB 与 y 轴的交点。
- `MathKit.ellipseOther(P,D,a,b)`：已知 P 在标准椭圆 x²/a²+y²/b²=1 上，沿非零方向 D，返回直线与椭圆的另一交点；只能用于已满足条件的点，切线时交点重合必须识别。
- `MathKit.fmt(value,digits=3)` 只负责显示小数，不能用于核心运算。
全部派生量放在 compute 中，draw/explain/inset 使用同一 d。可以使用原生 Math 和普通数组运算。explain 只返回少量 p、div.formula、span、b、sub、sup 等排版；不用反斜杠 LaTeX。最关键的中间变形必须写出，可增加一个步骤处理“代入方程→已知根→另一交点”，不能一句“联立可得”跳过。每个小问有完整结果，最值/范围附证明、定义域、取等或不可取说明。最终说明必须对应刚在图中观察过的关系。
