// The Workers runtime provides node:buffer (nodejs_compat); we only need its native base64 encoder.
declare module "node:buffer" {
  export const Buffer: { from(data: ArrayBuffer | Uint8Array): { toString(encoding: "base64"): string } };
}
