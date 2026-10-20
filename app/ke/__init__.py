"""M7 · KE 标注进度模块：每天收工后各组全量上传标注 CSV → 自动统计进度（五屏看板）。

与独立版工具 generate_report.py 同源：
- metrics.py  = 段1「指标计算与数据校验」移植（输入从 CSV 行改为数据库行，口径一致）
- template.py = 段2「图表渲染」移植（网页版增加应用导航/组选择/上传横幅三处注入点）
- service.py  = CSV 解析 + 按组全量替换式上传 + 查询组装
- router.py   = 页面与 API 路由
"""
