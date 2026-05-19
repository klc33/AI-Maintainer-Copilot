# docker/widget.Dockerfile
# Stage 1: Build the widget bundle
FROM node:20-alpine AS builder

WORKDIR /app

# Install pnpm or use npm (you can choose; here we'll use npm for simplicity)
COPY widget/package.json widget/package-lock.json* ./

# If you don't have package-lock.json yet, npm install will generate one
RUN npm install

COPY widget/ .

# Build the widget (adjust the build command to match your package.json scripts)
# This produces a dist/ folder
RUN npm run build

# Stage 2: Serve built files with nginx
FROM nginx:alpine

# Copy built widget files
COPY --from=builder /app/dist /usr/share/nginx/html/widget

# Copy loader script from widget/public/loader.js to /usr/share/nginx/html/widget.js
COPY widget/public/loader.js /usr/share/nginx/html/widget.js

# Copy a default nginx config (optional; the default nginx will serve everything in /usr/share/nginx/html)
# For now, we'll just use the default config and expose port 8080
EXPOSE 8080

# Nginx default config listens on port 80, but we can change it with a custom config.
# Since our compose maps 8080:8080, we'll add a custom config.
COPY docker/widget-nginx.conf /etc/nginx/conf.d/default.conf

CMD ["nginx", "-g", "daemon off;"]