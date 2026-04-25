import { MongoClient } from "mongodb";
const mongoUri = process.env.MONGODB_URI;
if (!mongoUri) {
    throw new Error("MONGODB_URI is required");
}
const dbName = process.env.MONGODB_DB_NAME || "timehole";
const client = new MongoClient(mongoUri);
let initialized = false;
export async function initDb() {
    if (initialized) {
        return;
    }
    await client.connect();
    await usersCollection().createIndex({ username: 1 }, { unique: true });
    initialized = true;
}
export function usersCollection() {
    return client.db(dbName).collection("users");
}
