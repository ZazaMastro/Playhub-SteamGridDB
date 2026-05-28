import { FC, ReactNode } from 'react';
export declare const SettingsContext: import("react").Context<{}>;
type SettingsContextType = {
    set: (key: any, value: any, immediate?: boolean) => void;
    get: (key: any, fallback: any) => Promise<any>;
    settings: any;
};
export declare const SettingsProvider: FC<{
    children: ReactNode;
}>;
export declare const useSettings: () => SettingsContextType;
export default useSettings;
