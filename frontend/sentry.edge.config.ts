import * as Sentry from "@sentry/nextjs";

Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN || undefined,
  release: process.env.NEXT_PUBLIC_RELEASE ?? "dev",
  environment: process.env.NEXT_PUBLIC_ENVIRONMENT ?? "local",
  tracesSampleRate: 0,
  sendDefaultPii: false,
});
