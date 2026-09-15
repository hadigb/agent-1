package com.datin.esb.enums;

/** کانال تراکنش */
public enum TransactionChannel {
    POS(1), ATM(2), BATCH(3), TELLER(4), INTERNET(5), TELEPHONE(6), MOBILE(7), PINPAD(8), KIOSK(9), CARDPRESENTKIOSK(10);

    private final int code;
    TransactionChannel(int code) { this.code = code; }
    public int getCode() { return code; }
}
