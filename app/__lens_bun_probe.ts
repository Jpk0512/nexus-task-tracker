import { enqueue, registerCron } from "./packages/jobs/src/init.ts";

const h = await enqueue("nope-unknown-job", { x: 1 }, { delayMs: 0 });
console.log("ENQUEUE_HANDLE_ID_TYPE:", typeof h.id, "len>0:", h.id.length > 0);
const d = registerCron("probe", "0 1 */2 * *", async () => {});
console.log("CRON_DESC:", JSON.stringify(d));
