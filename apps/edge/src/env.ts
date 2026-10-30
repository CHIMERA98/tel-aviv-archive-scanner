export interface Env {
  DB: D1Database;
  FILES: R2Bucket;
  ASSETS: Fetcher;
  EMAIL?: SendEmail;

  PUBLIC_URL: string;
  EMAIL_FROM: string;
  TURNSTILE_SITE_KEY: string;
  TURNSTILE_HOSTNAMES: string; // comma separated
  LOOKUP_URL: string; // Cloud Run lookup service
  GCP_PROJECT: string;
  GCP_REGION: string;
  GCP_JOB: string;
  MAX_RUNNING: string;
  WEEKLY_QUOTA: string;
  DAILY_CAP: string;
  RETENTION_HOURS: string;

  TURNSTILE_SECRET: string;
  SCANNER_HMAC_KEY: string;
  SESSION_KEY: string;
  GCP_SA_KEY: string; // service account JSON
}

export type AppContext = { Bindings: Env; Variables: { body?: ArrayBuffer } };

export const num = (value: string | undefined, fallback: number): number => {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
};

export const now = (): number => Math.floor(Date.now() / 1000);
