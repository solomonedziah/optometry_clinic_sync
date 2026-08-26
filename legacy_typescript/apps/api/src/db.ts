import { Pool, type PoolConfig } from "pg";

export function createPool(connectionString: string): Pool {
  const config: PoolConfig = { connectionString };
  return new Pool(config);
}
