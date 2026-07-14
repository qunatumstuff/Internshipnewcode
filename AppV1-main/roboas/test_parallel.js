const { Configuration, OpenAIApi } = require("openai");

const configuration = new Configuration({ apiKey: process.env.OPENAI_API_KEY });
const openai = new OpenAIApi(configuration);

async function run() {
  const completion = await openai.createChatCompletion({
    model: "gpt-5.4-mini",
    messages: [
      {role: "system", content: "ROBOTIC ARM — PICK AND PLACE RULES:\n- When the user asks you to pick up a specific, unambiguous object, you MUST call the 'locate_object' tool.\n- MULTIPLE OBJECTS: If the user asks to pick up multiple objects in one request, you MUST call the 'locate_object' tool MULTIPLE TIMES in the same response.\n- CRITICAL SEQUENCE RULE: You must call the tool in the EXACT chronological order the items should be picked up."},
      {role: "user", content: "Could you pick up the red cube and then after that pick up the blue cube?"}
    ],
    tools: [
      {
        type: "function",
        function: {
          name: "locate_object",
          description: "Locate an object",
          parameters: {
            type: "object",
            properties: { target_name: { type: "string" } }
          }
        }
      }
    ],
    parallel_tool_calls: true
  });
  console.log(JSON.stringify(completion.data.choices[0].message, null, 2));
}
run();
