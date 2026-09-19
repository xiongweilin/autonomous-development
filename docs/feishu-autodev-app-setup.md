# Feishu Autonomous Development app setup

This is the manual checkpoint that cannot be completed by local code. Create a new enterprise
self-built Feishu app for Autonomous Development; do not reuse the existing Dify/operations bot,
its App ID, App Secret, open ID, state database or notification HMAC.

The local adapter is implemented in the independent `feishu-autodev-bridge` profile in
`D:\infrastructure\compose\feishu-gateway`. It uses the standalone `lark-channel-sdk` package
(`lark_channel.FeishuChannel`) while the existing profile continues to use its existing
`lark-oapi` path.

## Console steps

The exact console wording varies slightly between Feishu tenant editions. Use the following
sequence in the Feishu Open Platform:

1. Open the developer console and choose **Create enterprise self-built app**.
2. Name it `Autonomous Development` or `自主开发`; record the new App ID in the separate secret
   directory, never in Git or a task description.
3. Enable the **Bot** capability.
4. Under **Event subscriptions**, choose the **WebSocket / long connection** delivery method and
   subscribe to `im.message.receive_v1`.
5. Configure the interactive card callback used by the bot and make sure card actions are
   delivered to the same app. The adapter consumes the current `card.action.trigger` callback
   through the Channel SDK's `cardAction` event.
6. Grant only the tenant scopes needed for this P2P owner-only profile:

   - `im:message:send_as_bot` for bot replies and cards;
   - `im:message:readonly` for message-resource download (`GET /open-apis/im/v1/messages/{message_id}/resources/{file_key}`);
   - `im:message.p2p_msg:readonly` for P2P message receipt where this scope is exposed by the
     tenant console.

   The official Channel SDK quickstart describes bot message send/receive scopes such as
   `im:message` and `im:message:send_as_bot`; if the console presents the broader legacy
   `im:message` scope instead of the split read scope, select the smallest scope the tenant
   offers and record the effective scope list in the deployment record. Do not add group-message,
   contact, Drive, Docs or OCR scopes for this V1. Attachment download is implemented through the
   official message-resource API, not a private URL.
7. Restrict the app's availability to the owner account only, publish the app version, and
   install/re-install it in the tenant after every scope or event-subscription change.
8. In the app's credential page, copy the new App Secret directly into the secret helper prompt;
   never paste it into PowerShell profile, `.env`, a compose environment value, logs or Git.

## Owner identity

The bridge accepts only one sender identity and additionally requires a P2P conversation. Obtain
the owner's `open_id` in the context of this new app/tenant using the official app context or a
controlled message event. Do not reuse the old bot's configured open ID. Put the value in the
new profile's `owner_open_id` secret file.

The bot does not accept group messages, other senders, arbitrary commands, images, audio, OCR or
cloud-document URLs. Supported inputs are text, post/rich text, `.txt`, `.md`, `.docx` and PDFs
with extractable text. Encrypted, scanned and empty PDFs are rejected with a safe explanation.

## Local secret setup

From the `feishu-gateway` checkout, run the interactive helper as the Windows account that will
own the scheduled task:

```powershell
.\deploy\windows\set-autodev-feishu-secrets.ps1
```

The helper creates a separate ACL-protected directory containing only `app_id`, `app_secret`,
`owner_open_id` and `operator_hmac_secret`. It prints configuration status only. The operator
HMAC must match the control-plane `AUTODEV_OPERATOR_HMAC_SECRET_FILE`; it is not a Feishu secret.

## Verification checklist

Before accepting a real E2E run, verify all of the following without printing secret values:

- the new app's Bot capability is enabled;
- WebSocket long connection is enabled;
- `im.message.receive_v1` is subscribed;
- card action callback is enabled;
- effective scopes include send-as-bot, P2P receive and message-resource read;
- the app is published and installed for the owner;
- the new bridge `/healthz` is `200` and `/readyz` becomes `200` only after its WebSocket is up;
- the control-plane `/ready` check includes `operator-api` and remains independent of bridge
  availability;
- an owner P2P text requirement produces a confirmation card;
- a non-owner or group message is ignored;
- the existing bot still passes its regression suite and keeps its original state/ports.

The local implementation cannot mark this checkpoint complete until the app is created,
published, securely configured and exercised with a real owner P2P message.
