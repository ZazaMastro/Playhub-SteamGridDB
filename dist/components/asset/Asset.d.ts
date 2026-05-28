import { FC } from 'react';
import { FocusableProps, FooterLegendProps } from '@decky/ui';
export interface AssetProps extends FooterLegendProps, Omit<FocusableProps, 'children'> {
    assetType: SGDBAssetType;
    width: number;
    height: number;
    src: string;
    author?: any;
    isAnimated: boolean;
    isDownloading?: boolean;
    onActivate?: FocusableProps['onActivate'];
    scrollContainer?: Element;
    notes?: string;
    nsfw?: boolean;
    humor?: boolean;
    epilepsy?: boolean;
    onImgError?: React.ReactEventHandler<HTMLImageElement>;
}
declare const Asset: FC<AssetProps>;
export default Asset;
