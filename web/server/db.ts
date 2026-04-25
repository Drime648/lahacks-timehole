import { MongoClient, type Collection } from "mongodb";
import type { DnsLogDocument, UserDocument } from "./types.js";

const mongoUri = process.env.MONGODB_URI;
if (!mongoUri) {
  throw new Error("MONGODB_URI is required");
}

const dbName = process.env.MONGODB_DB_NAME || "timehole";
const client = new MongoClient(mongoUri);

let initialized = false;

export async function initDb(): Promise<void> {
  if (initialized) {
    return;
  }

  await client.connect();
  const indexes = await usersCollection().indexes();
  const hasLegacyEmailIndex = indexes.some((index) => index.name === "email_1");
  if (hasLegacyEmailIndex) {
    await usersCollection().dropIndex("email_1");
  }
  await usersCollection().createIndex({ username: 1 }, { unique: true });
  await dnsLogsCollection().createIndex({ sourceIp: 1, createdAt: -1 });
  initialized = true;
}

export function usersCollection(): Collection<UserDocument> {
  return client.db(dbName).collection<UserDocument>("users");
}

export function dnsLogsCollection(): Collection<DnsLogDocument> {
  return client.db(dbName).collection<DnsLogDocument>("dns_logs");
}
