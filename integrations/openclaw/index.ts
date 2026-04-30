/**
 * OpenClaw Plugin - Legacy Aify Service Integration
 *
 * Legacy reference for an older containerized service integration.
 * It is not the current aify-comms dashboard/control-plane integration.
 *
 * Tools are registered as agent capabilities.
 * Hooks enable automatic context injection and processing.
 */

interface PluginConfig {
  apiUrl: string;
  userId: string;
  autoQuery: boolean;
  autoProcess: boolean;
  debug: boolean;
}

interface PluginContext {
  config: PluginConfig;
  logger: {
    info: (msg: string) => void;
    error: (msg: string) => void;
    debug: (msg: string) => void;
  };
}

// ---------------------------------------------------------------------------
// API Helper
// ---------------------------------------------------------------------------

async function apiCall(
  config: PluginConfig,
  method: string,
  path: string,
  body?: any
): Promise<any> {
  const url = `${config.apiUrl}${path}`;
  const options: RequestInit = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body) {
    options.body = JSON.stringify(body);
  }

  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(`API error ${response.status}: ${await response.text()}`);
  }
  return response.json();
}

// ---------------------------------------------------------------------------
// Plugin Export
// ---------------------------------------------------------------------------

export default {
  /**
   * Called when the plugin is loaded. Verify service is reachable.
   */
  async start(ctx: PluginContext) {
    try {
      const info = await apiCall(ctx.config, "GET", "/info");
      ctx.logger.info(
        `Connected to ${info.name} v${info.version}: ${info.description}`
      );
    } catch (error: any) {
      ctx.logger.error(`Service unreachable at ${ctx.config.apiUrl}: ${error.message}`);
    }
  },

  /**
   * Called when the plugin is unloaded.
   */
  async stop(ctx: PluginContext) {
    ctx.logger.info("Legacy aify service plugin stopped");
  },

  /**
   * Registered tools - available to OpenClaw agents.
   */
  tools: {
    /**
     * Get service information and available capabilities.
     */
    service_info: {
      description: "Get information about the aify service",
      parameters: {},
      async handler(ctx: PluginContext) {
        return await apiCall(ctx.config, "GET", "/info");
      },
    },

    /**
     * Check service health.
     */
    service_health: {
      description: "Check aify service health",
      parameters: {},
      async handler(ctx: PluginContext) {
        return await apiCall(ctx.config, "GET", "/health");
      },
    },

    // TODO: Add service-specific tools here
    // Each tool maps to a REST API endpoint on your service.
    //
    // your_tool: {
    //   description: "What this tool does",
    //   parameters: {
    //     param1: { type: "string", description: "Description", required: true },
    //   },
    //   async handler(ctx: PluginContext, args: { param1: string }) {
    //     return await apiCall(ctx.config, "POST", "/api/v1/your-endpoint", args);
    //   },
    // },
  },

  /**
   * Event hooks - automatic context injection and processing.
   */
  hooks: {
    /**
     * Called before each agent turn. Injects service context if autoQuery is enabled.
     */
    async before_agent_start(ctx: PluginContext, event: { prompt: string }) {
      if (!ctx.config.autoQuery) return;

      try {
        // TODO: Implement auto-query logic for your service
        // Example: search for relevant context based on the user's prompt
        // const context = await apiCall(ctx.config, "POST", "/api/v1/search", {
        //   query: event.prompt.substring(0, 200),
        // });
        // return {
        //   contextInjection: `<service-context>\n${JSON.stringify(context)}\n</service-context>`,
        // };
      } catch (error: any) {
        if (ctx.config.debug) {
          ctx.logger.debug(`Auto-query failed: ${error.message}`);
        }
      }
    },

    /**
     * Called after each agent turn. Processes the conversation if autoProcess is enabled.
     */
    async agent_end(
      ctx: PluginContext,
      event: { userMessage: string; assistantMessage: string }
    ) {
      if (!ctx.config.autoProcess) return;

      try {
        // TODO: Implement auto-process logic for your service
        // Example: extract and store insights from the conversation
        // await apiCall(ctx.config, "POST", "/api/v1/process", {
        //   user_message: event.userMessage,
        //   assistant_message: event.assistantMessage,
        // });
      } catch (error: any) {
        if (ctx.config.debug) {
          ctx.logger.debug(`Auto-process failed: ${error.message}`);
        }
      }
    },
  },
};
