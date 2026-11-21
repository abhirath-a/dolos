import { computeNetworkLayout, type LayoutInput } from "./networkLayout";

self.onmessage = ({ data }: MessageEvent<LayoutInput>) => {
  self.postMessage(computeNetworkLayout(data));
};
