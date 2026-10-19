#!/usr/bin/env bash
# ==============================================================================
# 微信公众号转 RSS（路线 A：we-mp-rss）云端 VPS 一键自动化安装部署脚本
# 支持系统：Ubuntu 20.04+, Debian 11+, CentOS 7+, Rocky Linux / AlmaLinux
# ==============================================================================

set -e

GREEN="\033[32m"
RED="\033[31m"
YELLOW="\033[33m"
CYAN="\033[36m"
PLAIN="\033[0m"

echo -e "${CYAN}====================================================${PLAIN}"
echo -e "${GREEN}    微信公众号转 RSS 服务（we-mp-rss）一键部署脚本    ${PLAIN}"
echo -e "${CYAN}====================================================${PLAIN}"

# 1. 检查是否具有 root 权限
if [ "$(id -u)" != "0" ]; then
    echo -e "${RED}[错误] 请使用 root 用户或 sudo 执行此脚本！${PLAIN}"
    exit 1
fi

INSTALL_DIR="/opt/we-mp-rss"
mkdir -p "$INSTALL_DIR/data"
cd "$INSTALL_DIR"

# 2. 检测并安装 Docker 与 Docker Compose
echo -e "${YELLOW}[1/4] 检查 Docker 运行环境...${PLAIN}"
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}未检测到 Docker，正在通过官方脚本安装 Docker...${PLAIN}"
    curl -fsSL https://get.docker.com | bash
    systemctl enable docker
    systemctl start docker
    echo -e "${GREEN}Docker 安装完成！${PLAIN}"
else
    echo -e "${GREEN}Docker 已安装。${PLAIN}"
fi

# 检查 docker-compose 或 docker compose
DOCKER_COMPOSE_CMD=""
if docker compose version &> /dev/null; then
    DOCKER_COMPOSE_CMD="docker compose"
elif command -v docker-compose &> /dev/null; then
    DOCKER_COMPOSE_CMD="docker-compose"
else
    echo -e "${YELLOW}未检测到 Docker Compose，正在安装 docker-compose-plugin...${PLAIN}"
    if command -v apt-get &> /dev/null; then
        apt-get update && apt-get install -y docker-compose-plugin
    elif command -v yum &> /dev/null; then
        yum install -y docker-compose-plugin
    fi
    DOCKER_COMPOSE_CMD="docker compose"
fi

# 3. 写入 docker-compose.yml
echo -e "${YELLOW}[2/4] 配置 docker-compose.yml...${PLAIN}"
cat << 'EOF' > "$INSTALL_DIR/docker-compose.yml"
services:
  we-mp-rss:
    image: ghcr.io/rachelos/we-mp-rss:latest
    container_name: we-mp-rss
    restart: unless-stopped
    ports:
      - "8001:8001"
    volumes:
      - ./data:/app/data
    environment:
      - TZ=Asia/Shanghai
    deploy:
      resources:
        limits:
          memory: 512M
EOF

# 4. 拉取并启动容器
echo -e "${YELLOW}[3/4] 拉取镜像并启动容器...${PLAIN}"
$DOCKER_COMPOSE_CMD pull
$DOCKER_COMPOSE_CMD down || true
$DOCKER_COMPOSE_CMD up -d

# 5. 提示防火墙与安全组设置
echo -e "${YELLOW}[4/4] 检查运行状态与公网访问...${PLAIN}"
sleep 3
if docker ps | grep -q "we-mp-rss"; then
    # 获取本机公网 IP
    PUBLIC_IP=$(curl -s4 ifconfig.me || curl -s4 ip.sb || echo "<你的VPS公网IP>")
    
    echo -e ""
    echo -e "${GREEN}====================================================${PLAIN}"
    echo -e "${GREEN}  🎉 部署成功！we-mp-rss 正在后台运行中              ${PLAIN}"
    echo -e "${GREEN}====================================================${PLAIN}"
    echo -e "Web 管理面板访问地址: ${CYAN}http://${PUBLIC_IP}:8001${PLAIN}"
    echo -e ""
    echo -e "${YELLOW}⚠️ 重要提示：${PLAIN}"
    echo -e "1. 请务必在你的云厂商控制台（阿里云/腾讯云/华为云/AWS等）的【安全组/防火墙】中放行 ${CYAN}TCP 8001${PLAIN} 端口！"
    echo -e "2. 首次登录请访问上述地址，使用微信小号扫描【微信读书】二维码绑定授权。"
    echo -e "3. 绑定成功后，在面板中搜索添加你想要订阅的公众号即可生成 RSS 源。"
    echo -e "4. 管理命令："
    echo -e "   - 查看实时日志: cd $INSTALL_DIR && $DOCKER_COMPOSE_CMD logs -f"
    echo -e "   - 停止服务: cd $INSTALL_DIR && $DOCKER_COMPOSE_CMD down"
    echo -e "   - 启动服务: cd $INSTALL_DIR && $DOCKER_COMPOSE_CMD up -d"
else
    echo -e "${RED}[错误] 容器启动失败，请运行 'docker logs we-mp-rss' 查看日志排查！${PLAIN}"
    exit 1
fi
