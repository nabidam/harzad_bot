from utils.constants.keyboards import BACK_KEYBOARD, FIRST_KEYBOARD, SECOND_KEYBOARD, FINAL_KEYBOARD, HOME_KEYBOARD
from telegram import ReplyKeyboardMarkup


start_keyboard = [[FIRST_KEYBOARD]]
start_keyboard_rm = ReplyKeyboardMarkup(start_keyboard, resize_keyboard=True)

first_state_keyboard = [[SECOND_KEYBOARD], [BACK_KEYBOARD]]
first_state_keyboard_rm = ReplyKeyboardMarkup(first_state_keyboard, resize_keyboard=True)

second_state_keyboard = [[FINAL_KEYBOARD], [BACK_KEYBOARD], [HOME_KEYBOARD]]
second_state_keyboard_rm = ReplyKeyboardMarkup(second_state_keyboard, resize_keyboard=True)

final_state_keyboard = [[BACK_KEYBOARD], [HOME_KEYBOARD]]
final_state_keyboard_rm = ReplyKeyboardMarkup(final_state_keyboard, resize_keyboard=True)