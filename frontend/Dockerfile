# Cloud Run image for the Next.js console.
FROM node:20-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Next.js inlines NEXT_PUBLIC_* into the client bundle at build time, so this
# has to be set here rather than as a Cloud Run runtime env var. "/backend"
# routes every browser call through app/backend/[...path]/route.ts, the
# same-origin relay to the private backend Cloud Run service.
ENV NEXT_PUBLIC_API_URL=/backend
# Google Identity Services client ID — public by design (it's the audience
# value shown to the browser's sign-in button), so safe to bake in here.
ENV NEXT_PUBLIC_GOOGLE_CLIENT_ID=250789503197-lg2gr86ols7jeqrd05q5h1hhnjqcsop4.apps.googleusercontent.com
RUN npm run build

FROM node:20-slim AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

# Cloud Run injects $PORT; default to 8080 locally.
ENV PORT=8080
EXPOSE 8080
CMD ["node", "server.js"]
