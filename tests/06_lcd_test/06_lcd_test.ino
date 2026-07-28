#include <LiquidCrystal.h>

const int LCD_RS = 7;
const int LCD_ENABLE = 8;
const int LCD_D4 = 9;
const int LCD_D5 = 10;
const int LCD_D6 = 11;
const int LCD_D7 = 12;

LiquidCrystal lcd(
  LCD_RS,
  LCD_ENABLE,
  LCD_D4,
  LCD_D5,
  LCD_D6,
  LCD_D7
);

void setup() {
  lcd.begin(16, 2);

  lcd.setCursor(0, 0);
  lcd.print("AegisLab");

  lcd.setCursor(0, 1);
  lcd.print("LCD working");
}

void loop() {
}
