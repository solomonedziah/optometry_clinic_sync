import { buildApp } from "./app.js";
import { loadConfig } from "./config.js";
import { createPool } from "./db.js";
import { runMigrations } from "./migrate.js";

const config = loadConfig();
await runMigrations(config.DATABASE_URL);
const pool = createPool(config.DATABASE_URL);
const app = await buildApp(config, pool);

const close = async () => {
  await app.close();
  await pool.end();
};
process.on("SIGINT", close);
process.on("SIGTERM", close);

await app.listen({ host: config.HOST, port: config.PORT });
