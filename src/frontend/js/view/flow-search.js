import { candidateScan } from "../util/candidate-scan.js";

const scan = candidateScan('/paam/ledger/v1/flow', 'flow');
export const stopFlowRead = scan.stop;
export const resetFlowSearch = scan.reset;
export const flowReadBusy = scan.busy;
export const readFlowSearch = scan.read;
export const bindFlowSearch = scan.bind;
