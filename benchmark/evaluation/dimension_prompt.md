当前评分维度：{{DIMENSION}}
名称：{{LABEL}}
仅根据本维度的标准、原题与候选页面证据评分：
{{CRITERIA}}
评分锚点：1=严重缺陷；2=明显问题，需要较大修改；3=基本可用，仍有明显不足；4=良好，仅少量局部问题；5=完整清晰，无实质缺陷。本维度有更具体的锚点时以其为准。证据不足给 null 并说明缺失材料。
输出严格 JSON，无 Markdown 围栏：
{"dimension":"{{DIMENSION}}","score":1到5或null,"issues":["具体位置、现象、对本维度的影响"],"evidence":"具体证据及未验证部分","reference_concern":"没有则空字符串","summary":"简短评价"}
