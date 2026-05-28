import { FC, ReactNode } from 'react';
export declare const SGDB_API_KEY = "6465636b796c6f616465723432303639";
export declare const SGDB_API_BASE: string;
export type SGDBContextType = {
    appId: number | null;
    setAppId: React.Dispatch<React.SetStateAction<number | null>>;
    appOverview: AppStoreAppOverview;
    searchAssets: (assetType: SGDBAssetType, options: {
        gameId?: number | null;
        filters?: any;
        page?: number;
        signal?: AbortSignal;
    }) => Promise<Array<any>>;
    searchGames: (term: string) => Promise<Array<any>>;
    getSgdbGame: (sgdbGame: any) => Promise<any>;
    changeAsset: (data: string, assetType: SGDBAssetType | eAssetType) => Promise<void>;
    changeAssetFromUrl: (location: string, assetType: SGDBAssetType | eAssetType, path?: boolean) => Promise<void>;
    clearAsset: (assetType: SGDBAssetType | eAssetType) => Promise<void>;
};
export declare const SGDBContext: import("react").Context<{}>;
export declare const SGDBProvider: FC<{
    children: ReactNode;
}>;
export declare const useSGDB: () => SGDBContextType;
export default useSGDB;
