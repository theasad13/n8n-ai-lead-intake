// Format Error: turn the Error Trigger payload into one short alert message.
// Mode: Run Once for All Items. Lead contents are left out on purpose, so no
// personal data ends up in the alert channel.
return $input.all().map((item) => {
  const data = item.json || {};
  const execution = data.execution || {};
  const workflow = data.workflow || {};
  const error = execution.error || {};
  const message = String(error.message || 'Unknown error').slice(0, 500);
  const lines = [
    'Lead intake workflow failed',
    'Workflow: ' + (workflow.name || 'unknown'),
    'Failed node: ' + (execution.lastNodeExecuted || 'unknown'),
    'Error: ' + message,
  ];
  if (execution.url) lines.push('Execution: ' + execution.url);
  return {
    json: {
      alert_text: lines.join('\n'),
      workflow_name: workflow.name || '',
      failed_node: execution.lastNodeExecuted || '',
      error_message: message,
      execution_id: execution.id || '',
    },
  };
});
