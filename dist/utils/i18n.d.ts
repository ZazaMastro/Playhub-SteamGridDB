export declare const LANGS: {
    [key: string]: {
        name: string;
        strings: {
            [key: string]: string;
        };
        credit: string[];
    };
};
export declare const getCredits: (lang?: string) => string[];
export declare const getLanguageName: (lang?: string) => string;
/**
 * Very basic translation cause theres like 20 strings and i don't need anything more complex.
 *
 * @param {string} key Locale key
 * @param {string} originalString Original text
 * @param {boolean} steamToken If true, uses the key to query Steams token store.
 *    Good for actions like "Back" or "Cancel". Won't be dumped with the rest of the strings.
 *
 * @example
 * t('TITLE_FILTER_MODAL', 'Asset Filters')
 * @example
 * // if you need variables use .replace()
 * t('ACTION_REMOVE_GAME', 'Delete {gameName}').replaceAll('{gameName}', gameName)
 * @example
 * // Original Steam string
 * t('Button_Back', 'Back', true);
 */
declare const trans_string: (key: string, originalString: string, steamToken?: boolean) => string;
export default trans_string;
