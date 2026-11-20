import { DiscordSDK } from "@discord/embedded-app-sdk";

async function enter() {
  const clientId = location.hostname.split(".")[0];
  const discord = new DiscordSDK(clientId);
  await discord.ready();
  const { code } = await discord.commands.authorize({
    client_id: clientId, response_type: "code", prompt: "none", scope: ["identify"],
  });
  const response = await fetch("/gomoku/activity/admit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, instance: discord.instanceId }),
  });
  if (!response.ok) throw new Error("Could not join room");
  const { room, ticket } = await response.json();
  location.replace("/gomoku/" + encodeURIComponent(room) + "#ticket=" + encodeURIComponent(ticket));
}

enter().catch(() => {
  document.querySelector("[role=status]").textContent =
    "오목방에 연결할 수 없습니다. 방 카드에서 참가 또는 관전을 다시 눌러 주세요.";
});
