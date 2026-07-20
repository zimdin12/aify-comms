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
