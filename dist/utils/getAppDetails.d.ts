import { AppDetails } from '@decky/ui/dist/globals/steam-client/App';
/**
 * Tries to retrieve the app details from Steam.
 *
 * @param appId id to get details for.
 * @returns AppDetails if succeeded or null otherwise.
 */
export default function getAppDetails(appId: number): Promise<AppDetails | null>;
