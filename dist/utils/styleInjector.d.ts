export declare const addStyle: (id: string, css: string) => void;
export declare const removeStyle: (id: string) => void;
/**
  Updates the style if it exists, creates the style if not.
 */
export declare const updateStyle: (id: string, css: string) => void;
export declare const removeStyles: (...ids: string[]) => void;
