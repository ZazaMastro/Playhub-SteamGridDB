import { FC } from 'react';
import { AssetProps } from '../components/asset/Asset';
declare const DetailsModal: FC<{
    closeModal?: () => void;
    asset: any;
    assetType: SGDBAssetType;
    onAssetChange?: () => Promise<any>;
    assetProps?: Partial<AssetProps>;
}>;
export default DetailsModal;
