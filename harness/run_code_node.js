// Run one Code node from a workflow JSON file outside n8n, with stubbed n8n globals.
//
//   node harness/run_code_node.js <workflow.json> "<node name>" <input.json>
//
// input.json: { "items": [{...json...}], "nodes": { "Config": [{...}], ... } }
// "items" is what the node receives. "nodes" feeds $('Node Name'): .first(),
// .all(), and .item (the entry with the same index as the current item).
// Prints the node's output items as JSON on stdout.
'use strict';
const fs = require('fs');

async function main() {
  const [workflowPath, nodeName, inputPath] = process.argv.slice(2);
  if (!workflowPath || !nodeName || !inputPath) {
    console.error('usage: node run_code_node.js <workflow.json> "<node name>" <input.json>');
    process.exit(2);
  }
  const workflow = JSON.parse(fs.readFileSync(workflowPath, 'utf8'));
  const node = workflow.nodes.find((n) => n.name === nodeName);
  if (!node || node.type !== 'n8n-nodes-base.code') {
    console.error('no Code node named ' + nodeName);
    process.exit(2);
  }
  const input = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
  const items = (input.items || []).map((json) => ({ json }));
  const others = input.nodes || {};
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  const run = new AsyncFunction('$input', '$', '$json', node.parameters.jsCode);

  const lookup = (index) => (name) => {
    const list = (others[name] || []).map((json) => ({ json }));
    if (!list.length) throw new Error('harness: no data for $("' + name + '")');
    return { first: () => list[0], all: () => list, item: list[index] || list[0] };
  };

  let output;
  if (node.parameters.mode === 'runOnceForEachItem') {
    output = [];
    for (let i = 0; i < items.length; i++) {
      const result = await run({ item: items[i], all: () => items }, lookup(i), items[i].json);
      output.push(result);
    }
  } else {
    output = await run({ all: () => items, first: () => items[0] }, lookup(0), items[0] && items[0].json);
  }
  process.stdout.write(JSON.stringify(output.map((item) => item.json)));
}

main().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(1);
});
