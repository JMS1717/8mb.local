FROM node:20-alpine
WORKDIR /app
COPY package.json /app/package.json
RUN npm install
COPY . /app
ARG PUBLIC_BACKEND_URL=http://localhost:8000
ENV PUBLIC_BACKEND_URL=${PUBLIC_BACKEND_URL}
RUN npm run build
EXPOSE 5173
CMD ["npm", "run", "preview"]
