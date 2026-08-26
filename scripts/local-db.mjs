import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import EmbeddedPostgres from "embedded-postgres";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const databaseDirectory = path.join(projectRoot, ".local-postgres");
const databaseName = "clinic_sync";
const postgres = new EmbeddedPostgres({
  databaseDir: databaseDirectory,
  user: "clinic_sync",
  password: "clinic_sync_dev",
  port: 5434,
  persistent: true,
  onLog: () => undefined,
});

try {
  await fs.access(path.join(databaseDirectory, "PG_VERSION"));
} catch {
  console.log("Initializing local PostgreSQL 17...");
  await postgres.initialise();
}

await postgres.start();
const client = postgres.getPgClient();
await client.connect();
const existingDatabase = await client.query("SELECT 1 FROM pg_database WHERE datname = $1", [databaseName]);
await client.end();
if (!existingDatabase.rowCount) await postgres.createDatabase(databaseName);

console.log("Local PostgreSQL is ready on 127.0.0.1:5434.");
await new Promise(() => undefined);
