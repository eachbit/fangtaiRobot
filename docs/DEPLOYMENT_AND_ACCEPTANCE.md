# 部署与验收说明

版本：2026-08-22

## 一、运行环境

项目目录应包含：

```text
app/
public/
data/
server.py
Dockerfile
```

`data/` 目录应包含官方菜谱、用户健康档案和对话用例。数据文件属于运行所需的交付材料，不写入公开代码仓库。

## 二、本地运行

```powershell
python server.py
```

默认访问地址：

```text
http://127.0.0.1:8000/
```

设置监听地址和端口：

```powershell
$env:HOST = "0.0.0.0"
$env:PORT = "8000"
python server.py
```

## 三、Docker 部署

构建镜像：

```powershell
docker build -t fangtai-robot:latest .
```

启动容器：

```powershell
docker run --rm --name fangtai-robot -p 8000:8000 fangtai-robot:latest
```

检查服务：

```powershell
curl.exe http://127.0.0.1:8000/api/health
```

导出镜像：

```powershell
docker save fangtai-robot:latest -o fangtai-robot.tar
```

未配置外部模型时，镜像仍可完成菜谱推荐、营养计算、多轮对话和菜单回滚。

## 四、Linux systemd 部署

建议安装目录：

```text
/opt/fangtaiRobot
```

环境配置文件：

```text
/etc/fangtai-robot.env
```

环境配置文件应设置为仅管理员可读：

```bash
sudo chmod 600 /etc/fangtai-robot.env
```

服务文件示例：

```ini
[Unit]
Description=Fangtai personalized diet agent
After=network.target

[Service]
WorkingDirectory=/opt/fangtaiRobot
EnvironmentFile=/etc/fangtai-robot.env
ExecStart=/usr/bin/python3 /opt/fangtaiRobot/server.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

启用服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now fangtai-robot
sudo systemctl status fangtai-robot --no-pager
```

## 五、公网服务

公网访问地址：

```text
http://47.116.110.131:8000/
```

健康检查：

```powershell
curl.exe http://47.116.110.131:8000/api/health
```

推荐请求：

```powershell
curl.exe -X POST http://47.116.110.131:8000/api/recommend `
  -H "Content-Type: application/json" `
  -d '{"messages":["4个人吃午餐，先推荐4道菜。","我不吃鸡蛋，其他菜尽量别动。"]}'
```

## 六、验收项目

### 1. 服务可用性

- 健康检查返回 `status=ok`；
- 菜谱数量和用户档案数量正确；
- 推荐接口返回合法 JSON；
- 网页首页可以正常访问。

### 2. 推荐准确性

- 推荐菜品全部来自官方菜谱库；
- 过敏和忌口零违反；
- 菜品数量与请求一致；
- 人数和餐次识别正确；
- 多人健康条件得到处理；
- 营养结果包含总量和人均值。

### 3. 多轮一致性

- 追加约束时保留仍然合适的菜品；
- 局部修改只替换必要菜品；
- 完整历史重放结果与连续会话一致；
- 菜单版本可查询和回滚；
- 回滚后仍执行硬约束校验。

### 4. 性能与容错

- 普通请求优先使用本地规则；
- 外部模型调用具备超时限制；
- 外部模型不可用时接口仍返回本地结果；
- 记录平均响应时间、P95 和最大响应时间。

## 七、安全要求

- API Key 通过环境变量配置；
- 不在日志、截图和文档中记录真实密钥；
- 不将服务器密码提交到版本库；
- 不将健康档案和官方数据强制提交到公开仓库；
- 云服务器仅开放必要端口。

