// 破甲工作台 · preload —— 渲染层唯一的对外通道
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('pojia', {
  boot: () => ipcRenderer.invoke('pojia:boot'),
  status: (targets) => ipcRenderer.invoke('pojia:status', targets),
  test: (targets) => ipcRenderer.invoke('pojia:test', targets),
  route: (sentence) => ipcRenderer.invoke('pojia:route', sentence),
  tx: (argv) => ipcRenderer.invoke('pojia:tx', argv),
  diff: (argv) => ipcRenderer.invoke('pojia:diff', argv),
  run: (action, targets) => ipcRenderer.invoke('pojia:run', { action, targets }),
  abort: () => ipcRenderer.send('pojia:abort'),
  setPython: (exe) => ipcRenderer.invoke('pojia:setPython', exe),
  pickPython: () => ipcRenderer.invoke('pojia:pickPython'),
  openPath: (target) => ipcRenderer.invoke('pojia:openPath', target),
  readLog: () => ipcRenderer.invoke('pojia:readLog'),
  openConsole: () => ipcRenderer.invoke('pojia:openConsole'),
  onLog: (cb) => { ipcRenderer.on('pojia:log', (_e, evt) => cb(evt)); },
  win: (act) => ipcRenderer.send('pojia:win', act),
});
