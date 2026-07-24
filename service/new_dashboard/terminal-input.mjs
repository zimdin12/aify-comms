export const createTerminalInputPoster = ({ api, terminalId, onError = () => {} }) => {
  let pending = Promise.resolve();
  return (data) => {
    pending = pending
      .then(() => api(`/terminals/${encodeURIComponent(terminalId)}/input`, {
        method: 'POST',
        body: JSON.stringify({ body: data, requestedBy: 'dashboard' }),
      }))
      .catch(onError);
    return pending;
  };
};

export const createTerminalInputHandler = ({ canInput, onBlocked, postInput }) => (data) => {
  if (!canInput()) {
    onBlocked();
    return undefined;
  }
  return postInput(data);
};

export const waitForTerminalSize = async ({ cols, rows, readSize, delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms)) }) => {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const current = await readSize();
    if (Number(current?.cols) === cols && Number(current?.rows) === rows) return;
    await delay(100);
  }
  throw new Error(`Terminal resize to ${cols}x${rows} was not applied`);
};

export const forceTerminalRepaint = async ({ cols, rows, resize, waitForSize }) => {
  const width = Math.max(20, Number(cols) || 80);
  const height = Math.max(5, Number(rows) || 24);
  const nudge = width === 20 ? 21 : width - 1;

  await resize(nudge, height);
  await waitForSize(nudge, height);
  await resize(width, height);
  await waitForSize(width, height);
};
