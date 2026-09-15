package com.datin.esb.error;

/** کدهای خطای سرویس‌ها */
public enum ErrorCode {
    SUCCESS(1, "عملیات با موفقیت انجام شد"),
    INVALID_INPUT(1038, "اطلاعات ورودی اشتباه است"),
    REQUIRED_PARAM_MISSING(2941, "مقداری برای پارامتر ورودی اجباری ارسال نشده است"),
    INVALID_CURRENCY(1163, "ارز انتخابی برای پرداخت نامعتبر است"),
    TOKEN_EXPIRED(3139, "توکن منقضی شده است"),
    DUPLICATE_TRANSACTION(1735, "تراکنش قبلا انجام شده و موفقیت آمیز بوده است"),
    INVALID_TRANSACTION_ID(1644, "شناسه ی تراکنش صحیح نمی باشد"),
    SOURCE_NOT_ALLOWED(2122, "حساب مبدا جزو حساب های مجاز مشتری نمی باشد"),
    UNBALANCED_DOCUMENT(4642, "مجموع مبالغ برداشتی با مجموع مبالغ واریزی برابر نمی باشد"),
    INVALID_DEPOSIT(1079, "شماره سپرده نادرست است"),
    DEPOSIT_WITHOUT_BILL_INFO(3181, "سپرده دارای اطلاعات قبض عملیات مالی نمی باشد"),
    TIMEOUT(804, "مدت زمان پردازش پیام منقضی شده است"),
    SERVICE_CALL_ERROR(805, "خطا در فراخوانی سرویس"),
    INVALID_TRANSACTION_NUMBER(1418, "شماره تراکنش نامعتبر است"),
    CORE_ERROR(1773, "خطا در کر");

    private final int code;
    private final String message;

    ErrorCode(int code, String message) {
        this.code = code;
        this.message = message;
    }

    public int getCode() { return code; }
    public String getMessage() { return message; }
}
