# Google 连通性测试网页（Python）

## 功能
- 手动点击按钮测试与 Google 连通性
- 输入间隔秒数并开启自动测试
- 手动和自动测试都会记录到本地 `connectivity_log.txt`
- 统计页基于日志展示图表（饼图/柱状图/折线图）

## 运行
```bash
pip install -r requirements.txt
python app.py
```

打开浏览器访问：
- 测试页：`http://127.0.0.1:5000/`
- 统计页：`http://127.0.0.1:5000/stats`
