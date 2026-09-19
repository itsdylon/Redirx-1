import { AsyncLocalStorage } from 'node:async_hooks';
import { createCipheriv, createDecipheriv, createHash, randomBytes } from 'node:crypto';

export const digest = (value) => createHash('sha256').update(value).digest('hex');

/** One encrypted store shared by all provider processes. No bearer values in indexes. */
export class AuthorizationStore {
  #context = new AsyncLocalStorage();
  constructor(pool, keys) {
    if (!keys.length || keys.some((key) => !Buffer.isBuffer(key) || key.length !== 32)) {
      throw new Error('Storage encryption keys must be 32 bytes');
    }
    this.pool = pool;
    this.keys = keys;
    const store = this;
    this.Adapter = class {
      constructor(model) { this.model = model; }
      upsert(id, payload, ttl) { return store.upsert(this.model, id, payload, ttl); }
      find(id) { return store.find(this.model, 'id_hash', id); }
      findByUid(uid) { return store.find(this.model, 'uid_hash', uid); }
      findByUserCode(code) { return store.find(this.model, 'user_code_hash', code); }
      destroy(id) { return store.destroy(this.model, id); }
      consume(id) { return store.consume(this.model, id); }
      revokeByGrantId(id) { return store.revoke(id); }
    };
  }

  async transaction(work) {
    if (this.#context.getStore()) return work();
    const client = await this.pool.connect();
    try {
      await client.query('BEGIN');
      await client.query("SET LOCAL lock_timeout = '5s'");
      await client.query("SET LOCAL statement_timeout = '15s'");
      // Serialize the provider's read/consume/rotate sequences across replicas.
      // Commit happens before Koa emits the response, including protocol errors
      // that intentionally revoke a replayed grant. This is a bounded-throughput
      // first release; replacing it needs equivalent concurrency acceptance.
      await client.query('SELECT pg_advisory_xact_lock(724319, 1)');
      const result = await this.#context.run(client, work);
      await client.query('COMMIT');
      return result;
    } catch (error) {
      await client.query('ROLLBACK').catch(() => {});
      throw error;
    } finally { client.release(); }
  }

  query(sql, values, write = false) {
    const client = this.#context.getStore();
    if (write && !client) throw new Error('Authorization writes require a transaction');
    return (client || this.pool).query(sql, values);
  }

  seal(model, idHash, payload) {
    const iv = randomBytes(12);
    const cipher = createCipheriv('aes-256-gcm', this.keys[0], iv);
    cipher.setAAD(Buffer.from(`${model}:${idHash}`));
    const body = Buffer.concat([cipher.update(JSON.stringify(payload)), cipher.final()]);
    return Buffer.concat([iv, cipher.getAuthTag(), body]);
  }

  open(model, idHash, payload) {
    for (const key of this.keys) {
      try {
        const cipher = createDecipheriv('aes-256-gcm', key, payload.subarray(0, 12));
        cipher.setAAD(Buffer.from(`${model}:${idHash}`));
        cipher.setAuthTag(payload.subarray(12, 28));
        return JSON.parse(Buffer.concat([cipher.update(payload.subarray(28)), cipher.final()]));
      } catch { /* Key rotation tries the retained decrypt-only keys. */ }
    }
    throw new Error('Authorization state could not be decrypted');
  }

  async upsert(model, id, payload, ttl) {
    const idHash = digest(id);
    await this.query(`INSERT INTO mcp_auth.objects
      (model,id_hash,payload,uid_hash,user_code_hash,grant_hash,expires_at)
      VALUES ($1,$2,$3,$4,$5,$6,CASE WHEN $7::integer IS NULL THEN NULL ELSE now()+$7*interval '1 second' END)
      ON CONFLICT (model,id_hash) DO UPDATE SET payload=EXCLUDED.payload,
      uid_hash=EXCLUDED.uid_hash,user_code_hash=EXCLUDED.user_code_hash,
      grant_hash=EXCLUDED.grant_hash,expires_at=EXCLUDED.expires_at`,
    [model, idHash, this.seal(model, idHash, payload), payload.uid ? digest(payload.uid) : null,
      payload.userCode ? digest(payload.userCode) : null, payload.grantId ? digest(payload.grantId) : null,
      ttl ?? null], true);
  }

  async find(model, column, id) {
    if (!['id_hash', 'uid_hash', 'user_code_hash'].includes(column)) throw new Error('Invalid lookup');
    const { rows } = await this.query(`SELECT id_hash,payload,consumed FROM mcp_auth.objects
      WHERE model=$1 AND ${column}=$2 AND (expires_at IS NULL OR expires_at>now())`, [model, digest(id)]);
    if (!rows.length) return undefined;
    const payload = this.open(model, rows[0].id_hash, rows[0].payload);
    if (rows[0].consumed !== null) payload.consumed = Number(rows[0].consumed);
    return payload;
  }

  async consume(model, id) {
    const { rowCount } = await this.query(`UPDATE mcp_auth.objects SET consumed=extract(epoch FROM now())::bigint
      WHERE model=$1 AND id_hash=$2 AND consumed IS NULL AND (expires_at IS NULL OR expires_at>now())`,
    [model, digest(id)], true);
    if (rowCount !== 1) throw new Error('Authorization state already used or expired');
  }

  async destroy(model, id) {
    await this.query('DELETE FROM mcp_auth.objects WHERE model=$1 AND id_hash=$2', [model, digest(id)], true);
  }

  async revoke(grantId) {
    await this.query('DELETE FROM mcp_auth.objects WHERE grant_hash=$1 OR (model=$2 AND id_hash=$1)',
      [digest(grantId), 'Grant'], true);
  }

  async allow(bucket, limit, seconds = 60) {
    const { rows } = await this.query(`INSERT INTO mcp_auth.rate_limits(bucket_hash,count,expires_at)
      VALUES($1,1,now()+$2*interval '1 second') ON CONFLICT(bucket_hash) DO UPDATE
      SET count=CASE WHEN rate_limits.expires_at<=now() THEN 1 ELSE rate_limits.count+1 END,
      expires_at=CASE WHEN rate_limits.expires_at<=now() THEN EXCLUDED.expires_at ELSE rate_limits.expires_at END
      RETURNING count`, [digest(bucket), seconds], true);
    return rows[0].count <= limit;
  }

  async cleanup() {
    await this.query(`DELETE FROM mcp_auth.objects WHERE (model,id_hash) IN
      (SELECT model,id_hash FROM mcp_auth.objects WHERE expires_at<=now() LIMIT 1000)`, [], true);
    await this.query('DELETE FROM mcp_auth.rate_limits WHERE expires_at<=now()', [], true);
  }
}
