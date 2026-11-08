import fs from 'node:fs';
import path from 'node:path';

/** A fixture brain, not a model. Only an actual controller turn/start can call respond. */
export class TurnBrain {
  attach(client, directory) { this.client = client; this.directory = directory; }

  async respond(params) {
    const text = (params?.input ?? []).filter(item => item.type === 'text').map(item => item.text ?? '').join('\n');
    const message = text.split('[MESSAGE]').at(-1);
    const nonce = /E4-PROBE-([0-9a-f]+)/.exec(message)?.[1];
    if (!nonce) return;
    const parent = /^MessageId:\s*(\S+)/m.exec(message)?.[1];
    if (!parent || !this.client || !process.env.AIFY_AGENT_ID || !process.env.STAGING_REPLY_TO) {
      throw new Error('staging turn lacks its real message identity or MCP client');
    }
    const inReplyTo = process.env.STAGING_BREAK_REPLY_LINK === '1' ? process.env.STAGING_DECOY_MESSAGE : parent;
    const result = await this.client.callTool({ name: 'comms_send', arguments: {
      from: process.env.AIFY_AGENT_ID, to: process.env.STAGING_REPLY_TO, type: 'response',
      subject: 'Staging work-loop reply', body: `E4-REPLY-${nonce}`, inReplyTo, requireReply: false,
    } });
    fs.appendFileSync(path.join(this.directory, 'turns.jsonl'), JSON.stringify({ pid: process.pid,
      agentId: process.env.AIFY_AGENT_ID, nonce, deliveredMessageId: parent, sentParent: inReplyTo,
      toolError: result.isError === true }) + '\n');
    if (result.isError) throw new Error('staging MCP reply was refused');
  }
}
export const brain = new TurnBrain();
