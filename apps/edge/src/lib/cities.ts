import type { Env } from "../env";

export type City = "tel-aviv" | "haifa";
export const CITIES: City[] = ["tel-aviv", "haifa"];

export const parseCity = (value: unknown): City | null => (CITIES.includes(value as City) ? (value as City) : null);

/** The Haifa host serves Haifa; every other host (main domain, workers.dev, tests) serves Tel Aviv. */
export const cityFromHost = (env: Env, url: string): City => {
  const host = new URL(url).hostname;
  return (env.HAIFA_URL && host === new URL(env.HAIFA_URL).hostname) || host.startsWith("haifa.") ? "haifa" : "tel-aviv";
};

/** "חיפה" or "Haifa" in an address picks Haifa wherever it was typed. */
export const detectCity = (text: string, fallback: City): City => (/חיפה|haifa/i.test(text) ? "haifa" : fallback);

export const baseUrl = (env: Env, city: City): string => (city === "haifa" && env.HAIFA_URL ? env.HAIFA_URL : env.PUBLIC_URL);
