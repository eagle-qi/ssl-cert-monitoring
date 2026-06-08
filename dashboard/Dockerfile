# 构建阶段
FROM node:18-alpine AS builder

WORKDIR /app

# 接收 build args
ARG VITE_AGENT_SERVER_URL=http://127.0.0.1:8090
ENV VITE_AGENT_SERVER_URL=$VITE_AGENT_SERVER_URL

# 复制依赖文件
COPY package*.json ./
RUN npm install

# 复制源代码
COPY . .

# 构建应用
RUN npm run build

# 生产阶段
FROM nginx:1.25-alpine

# 复制构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

# 复制nginx配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 暴露端口
EXPOSE 80

# 启动nginx
CMD ["nginx", "-g", "daemon off;"]
